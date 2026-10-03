import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CORE=Path(__file__).resolve().parents[2]/'core'
sys.path.insert(0,str(CORE))
os.environ['PYTHONPATH']=str(CORE)


def run():
    from ev.paths import get_paths
    config_path=get_paths().config_file
    with tempfile.TemporaryDirectory(prefix='carlos-audio-test-') as directory:
        root = Path(directory)
        runtime = root / 'run'
        runtime.mkdir(mode=0o700)
        env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), XDG_CONFIG_HOME=str(root / 'config'),
                   XDG_DATA_HOME=str(root / 'data'), XDG_CACHE_HOME=str(root / 'cache'),
                   PIPEWIRE_CONFIG_DIR=str(root), PIPEWIRE_CONFIG_PREFIX='',
                   PIPEWIRE_RUNTIME_DIR=str(runtime), PIPEWIRE_REMOTE='pipewire-0',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(root / 'no-session-bus'),
                   PULSE_SERVER='unix:' + str(runtime / 'pulse/native'))
        server = root / 'pipewire.conf'
        shutil.copyfile('/usr/share/pipewire/client.conf', root / 'client.conf')
        server.write_text('''context.properties = { core.daemon = true core.name = pipewire-0 }
context.spa-libs = { support.* = support/libspa-support audio.convert.* = audioconvert/libspa-audioconvert }
context.modules = [
 { name = libpipewire-module-protocol-native }
 { name = libpipewire-module-spa-node-factory }
 { name = libpipewire-module-client-node }
 { name = libpipewire-module-adapter }
 { name = libpipewire-module-metadata }
 { name = libpipewire-module-access }
]
context.objects = [ { factory = metadata args = { metadata.name = default } } ]
''')
        pulse = root / 'pulse.conf'
        pulse.write_text('''context.spa-libs = { support.* = support/libspa-support audio.convert.* = audioconvert/libspa-audioconvert }
context.modules = [
 { name = libpipewire-module-protocol-native }
 { name = libpipewire-module-client-node }
 { name = libpipewire-module-adapter }
 { name = libpipewire-module-metadata }
 { name = libpipewire-module-protocol-pulse args = { server.address = [ "unix:native" ] } }
]
''')

        def pactl(*args):
            result = subprocess.run(['pactl', *args], env=env, text=True,
                                    capture_output=True, timeout=2)
            if result.returncode:
                raise RuntimeError(result.stderr)
            return result.stdout.strip()

        children = []
        logs = root / 'server.log'
        with logs.open('w') as log:
            try:
                children.append(subprocess.Popen(['pipewire', '-c', server.name], env=env,
                                                 stdout=log, stderr=log))
                deadline = time.monotonic() + 4
                while not (runtime / 'pipewire-0').exists():
                    if time.monotonic() > deadline or children[0].poll() is not None:
                        raise RuntimeError('Private PipeWire failed: ' + logs.read_text())
                    time.sleep(.02)
                children.append(subprocess.Popen(['pipewire-pulse', '-c', pulse.name], env=env,
                                                 stdout=log, stderr=log))
                while not (runtime / 'pulse/native').exists():
                    if time.monotonic() > deadline or children[1].poll() is not None:
                        raise RuntimeError('Private Pulse server failed: ' + logs.read_text())
                    time.sleep(.02)
                # This environment belongs to the fixture process only.
                os.environ.update(env)
                pactl('load-module', 'module-null-sink', 'sink_name=carlos_speech_fixture',
                      'channels=1', 'channel_map=mono')
                pactl('set-default-sink', 'carlos_speech_fixture')
                configured = subprocess.run(['pw-metadata', '-n', 'default', '0',
                                             'default.audio.sink', '{"name":"carlos_speech_fixture"}',
                                             'Spa:String:JSON'], env=env, capture_output=True,
                                            text=True, timeout=2)
                assert configured.returncode == 0
                asyncio.run(check_speech(root,runtime,pactl,config_path))

            finally:
                for child in reversed(children):
                    if child.poll() is None:
                        child.terminate()
                    try:
                        child.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=3)


async def check_speech(root,runtime,pactl,config_path):
    from ev.config import DEFAULT_CONFIG
    from ev.paths import Paths
    from ev.service import CarlosCore
    from ev.cli import request
    import copy
    paths=Paths(*(root/name for name in ('config/ev','data/ev','state/ev','cache/ev','run/ev')))
    paths.ensure()
    config=copy.deepcopy(DEFAULT_CONFIG)
    user=json.loads(config_path.read_text())
    config['voice']=user['voice']
    config['voice']['wake']['enabled']=False
    config['voice']['echo_cancel']=False
    config['voice']['tts']['duck_media']=False
    config['voice']['tts']['output_device']='carlos_speech_fixture'
    config['assistant']['wake_enabled']=False
    paths.config_file.write_text(json.dumps(config))
    core=CarlosCore(paths=paths)
    speech=None
    report={'scope':'Actual temporary Core IPC and Piper/paplay playback on an isolated null sink; explicit typed stop, not acoustic barge-in',
            'host_playback_touched':False,'host_microphone_touched':False}
    try:
        await core.ipc.start()
        text=('This is a disposable Carlos speech stream. The test will stop this private playback. '
              'It should not finish all of these sentences after the stop request. ')*3
        speech=asyncio.create_task(core.voice.speak(text,'owned-speech',False))
        async with asyncio.timeout(15):
            while core.voice.tts_process is None or not core.voice.speaking:
                if speech.done():raise RuntimeError('Speech finished before native playback: '+str(speech.result()))
                await asyncio.sleep(.005)
            player=core.voice.tts_process
            while not json.loads(await asyncio.to_thread(pactl,'-f','json','list','sink-inputs')):
                if speech.done():raise RuntimeError('No native null-sink playback observed')
                await asyncio.sleep(.01)
        assert player.returncode is None
        started=time.monotonic()
        stopped=await request('command.submit',{'text':'hold up','speak':False})
        elapsed=(time.monotonic()-started)*1000
        assert stopped['type']=='response' and stopped['payload']['status']=='conversation_ended',stopped['type']
        outcome=await asyncio.wait_for(speech,3)
        assert outcome['status']=='cancelled',outcome
        assert player.returncode is not None
        assert core.state.current.value=='DORMANT'
        assert not core.voice.speaking and not core.voice.speech_pending and core.voice.tts_process is None
        next_request=await request('command.submit',{'text':'what time is it?','speak':False})
        assert next_request['type']=='response' and next_request['payload']['status']=='completed'
        report.update(stop_request_ms=round(elapsed,3),player_reaped=True,
                      measured_player_stop_ms=core.voice.diagnostics.get('speech_stop_latency_ms'),
                      latency_scope=core.voice.diagnostics.get('speech_stop_latency_scope'),
                      pending_speech_cleared=True,next_read_only_request_completed=True,
                      acoustic_latency_verified=False,playback_device='private null sink')
        assert core.voice.diagnostics['barge_in_latency_ms'] is None
    finally:
        if speech is not None and not speech.done():speech.cancel();await asyncio.gather(speech,return_exceptions=True)
        await core.voice.close()
        await core.ipc.stop()
        if hasattr(core.brain.provider,'close'):
            await core.brain.provider.close()
        core.memory.close();core.task_journal.close();core.daily.close()
        report['owned_worker_cleanup_completed']=True
    print(json.dumps(report),flush=True)


if __name__ == '__main__':
    run()
