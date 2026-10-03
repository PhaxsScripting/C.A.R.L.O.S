import asyncio
import copy
import json
import socket
import sys
import time

import psutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'core'))


async def check_privacy(root, runtime, pactl, config_path):
    from ev.config import DEFAULT_CONFIG
    from ev.paths import Paths
    from ev.service import CarlosCore

    config = copy.deepcopy(DEFAULT_CONFIG)
    config['voice'] = json.loads(config_path.read_text())['voice']
    voice = config['voice']
    voice['echo_cancel'] = False
    voice['tts']['duck_media'] = False
    voice['wake']['enabled'] = True
    voice['wake']['acknowledgement_chime'] = False
    sources = json.loads(pactl('-f', 'json', 'list', 'sources'))
    assert len(sources) == 1, sources
    source = sources[0]['name']
    assert source == 'carlos_speech_fixture.monitor', source
    voice['microphone_source'] = source
    reserved = []
    core = None
    writer = None
    owned = []
    child_pids = {child.pid for child in psutil.Process().children()}
    report = {'scope': 'Actual private Core IPC, local workers and capture from a private null-sink monitor',
              'host_microphone_used': False, 'host_playback_changed': False,
              'acoustic_acceptance': False}
    try:
        for settings in (voice['stt'], voice.setdefault('preview_stt', {})):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(('127.0.0.1', 0))
            reserved.append(listener)
            settings['server_port'] = listener.getsockname()[1]
        paths = Paths(*(root / name for name in ('config/ev', 'data/ev', 'state/ev', 'cache/ev', 'run/ev')))
        paths.ensure()
        paths.config_file.write_text(json.dumps(config))
        core = CarlosCore(paths=paths)
        for listener in reserved:
            listener.close()
        await core.ipc.start()
        reader, writer = await asyncio.open_unix_connection(str(paths.socket))
        await reader.readline()
        sequence = 0

        async def request(kind, payload=None):
            nonlocal sequence
            sequence += 1
            request_id = f'privacy-{sequence}'
            writer.write((json.dumps({'id': request_id, 'type': kind, 'payload': payload or {}}) + '\n').encode())
            await writer.drain()
            async with asyncio.timeout(15):
                while True:
                    line = await reader.readline()
                    assert line, 'Private Core closed the connection'
                    response = json.loads(line)
                    if response.get('id') == request_id:
                        return response

        async def ambient_ready():
            async with asyncio.timeout(20):
                while not (core.voice.wake.running and core.voice.wake_audio_process is not None
                           and core.voice.wake_audio_process.returncode is None):
                    await asyncio.sleep(.01)
            return core.voice.wake.process, core.voice.wake_audio_process

        await core.voice.prewarm()
        assert core.voice.stt._server_process is not None
        assert core.voice.preview_stt._server_process is not None
        assert core.voice.neural_vad.process is not None
        owned.extend((core.voice.stt._server_process, core.voice.preview_stt._server_process,
                      core.voice.neural_vad.process))
        await core.voice.start()
        wake, recorder = await ambient_ready()
        owned.extend((wake, recorder))
        assert core.voice.diagnostics['microphone'] == source
        assert all(process.returncode is None for process in owned)
        started = time.monotonic()
        response = await request('carlos.privacy.set', {'mode': 'DO NOT LISTEN'})
        assert response['type'] == 'response' and response['payload']['mode'] == 'DO NOT LISTEN'
        mute_ms = (time.monotonic() - started) * 1000
        assert all(process.returncode is not None for process in owned)
        assert core.voice.wake_audio_process is None and not core.voice.wake.running
        assert core.voice.stt._server_process is None and core.voice.preview_stt._server_process is None
        assert core.voice.neural_vad.process is None and not core.voice.capture_active
        assert not core.voice.capture_pcm and not core.voice.wake_buffer
        diagnostics = await request('voice.diagnostics')
        assert diagnostics['payload']['voice']['privacy_mode'] is True
        assert diagnostics['payload']['voice']['diagnostics']['wake_state'] == 'PRIVATE'
        refused = await request('voice.capture.start')
        assert refused['type'] == 'error', refused
        assert refused['payload']['message'] == 'Microphone privacy mode is enabled', refused
        assert core.voice.capture_process is None and not core.voice.capture_active
        after = json.loads(paths.config_file.read_text())
        after['carlos']['privacy_mode'] = config['carlos']['privacy_mode']
        assert after == config
        response = await request('carlos.privacy.set', {'mode': 'NORMAL'})
        assert response['type'] == 'response' and response['payload']['mode'] == 'NORMAL'
        next_wake, next_recorder = await ambient_ready()
        owned.extend((next_wake, next_recorder))
        assert next_wake.pid != wake.pid and next_recorder.pid != recorder.pid
        report.update(actual_stt_preview_vad_wake_capture_reaped=True,
                      private_capture_refused_while_muted=True, mute_transition_ms=round(mute_ms, 3),
                      privacy_diagnostics_observed=True, private_settings_preserved=True,
                      normal_mode_restarted_owned_ambient_workers=True)
    finally:
        for listener in reserved:
            listener.close()
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        if core is not None:
            synthesis_worker = core.voice.tts.piper._worker
            if synthesis_worker is not None:
                owned.append(synthesis_worker)
            await core.voice.close()
            await core.ipc.stop()
            if hasattr(core.brain.provider, 'close'):
                await core.brain.provider.close()
            core.daily.close()
            core.memory.close()
            core.task_journal.close()
        assert all(process.returncode is not None for process in owned)
        assert not {child.pid for child in psutil.Process().children()} - child_pids
        report['owned_worker_cleanup_completed'] = True
        report['no_new_child_remaining_after_core_cleanup'] = True
    return report


if __name__ == '__main__':
    from speech_stop_live import run
    run(check=check_privacy, monitor=True)
