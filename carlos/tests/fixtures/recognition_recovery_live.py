import asyncio
import copy
import json
import re
import socket
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'core'))


async def check(root, voice):
    from ev.voice.stt import WhisperCppAdapter
    from ev.voice.tts import TtsRouter
    from ev.voice.normalization import normalize_transcript

    config = copy.deepcopy(voice['stt'])
    tts = TtsRouter(copy.deepcopy(voice['tts']))
    runtime = root / 'recognition'
    runtime.mkdir(mode=0o700)
    adapter = None
    resampler = None
    workers = []
    report = {'scope': 'Owned local recognition worker recovery using fixed generated PCM; no microphone, playback or desktop action',
              'host_workers_touched': False, 'acoustic_acceptance': False}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
            reserved.bind(('127.0.0.1', 0))
            reserved.listen(2)
            config['server_port'] = reserved.getsockname()[1]
            adapter = WhisperCppAdapter(config, runtime)
            try:
                await adapter.prewarm()
            except RuntimeError as error:
                assert 'occupied by another process' in str(error), error
            else:
                raise AssertionError('An unowned listener was accepted')
            assert reserved.fileno() >= 0 and adapter._server_process is None
            assert not adapter._ownership_path.exists()
            report['unowned_listener_refused_without_mutation'] = True

        audio = await tts.synthesize('What is my CPU usage?')
        assert audio.engine == 'piper' and audio.channels == 1 and audio.sample_width == 2
        resampler = await asyncio.create_subprocess_exec(
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-f', 's16le', '-ar', str(audio.sample_rate), '-ac', '1', '-i', 'pipe:0',
            '-ar', '16000', '-f', 's16le', 'pipe:1',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        pcm, error = await asyncio.wait_for(resampler.communicate(audio.pcm), 5)
        assert resampler.returncode == 0, error

        async def transcribe():
            result = await adapter.transcribe(pcm)
            text = normalize_transcript(result.raw)
            assert re.fullmatch(r'(?:what\s+is|what[\x27\u2019]s)\s+my\s+cpu\s+usage[?.!]*', text, re.I), text
            assert not list(runtime.glob('stt-*.wav'))
            worker = adapter._server_process
            record = adapter._server_ownership
            assert worker is not None and worker.returncode is None and record is not None
            workers.append(worker)
            assert record.server.pid == worker.pid and await adapter._managed_server_ready(record)
            return result

        first = await transcribe()
        crashed = adapter._server_process
        crashed.kill()
        await asyncio.wait_for(crashed.wait(), 3)
        started = time.monotonic()
        recovered = await transcribe()
        recovery_ms = (time.monotonic() - started) * 1000
        replacement = adapter._server_process
        assert replacement is not crashed and replacement.pid != crashed.pid
        report.update(first_transcription_ms=round(first.latency_ms, 3),
                      recovery_transcription_ms=round(recovery_ms, 3),
                      replacement_listener_identity_verified=True,
                      crashed_worker_reaped=True, fixed_query_recognized_twice=True,
                      transcript_variants=[normalize_transcript(first.raw), normalize_transcript(recovered.raw)],
                      next_server_request_completed=True, command_submitted=False)
    finally:
        if resampler is not None and resampler.returncode is None:
            resampler.kill()
            await resampler.wait()
        if adapter is not None:
            await adapter.close()
        await tts.close()
        assert all(worker.returncode is not None for worker in workers)
        assert adapter is None or not adapter._ownership_path.exists()
        report['owned_workers_reaped'] = True
    return report


def run():
    from ev.paths import get_paths

    voice = json.loads(get_paths().config_file.read_text())['voice']
    assert voice['stt'].get('persistent_server', True), 'This fixture checks persistent recognition'
    with tempfile.TemporaryDirectory(prefix='carlos-recognition-test-') as directory:
        report = asyncio.run(check(Path(directory), voice))
    report['temporary_runtime_removed'] = True
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    run()
