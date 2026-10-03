import array
import asyncio
import copy
import fcntl
import json
import signal
import sys
import termios
import time

import psutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'core'))


async def check(config):
    from ev.voice.tts import PiperAdapter

    adapter = PiperAdapter(copy.deepcopy(config))
    workers = []
    child_pids = {child.pid for child in psutil.Process().children()}
    pending = None
    report = {'scope': 'Actual owned Piper worker and pipe; crash during a queued synthesis request',
              'microphone_used': False, 'audio_played': False, 'acoustic_acceptance': False,
              'host_workers_touched': False}
    try:
        assert config.get('persistent_worker', True), 'This fixture checks persistent synthesis'
        await adapter.prewarm()
        first_worker = adapter._worker
        assert first_worker is not None and first_worker.returncode is None
        workers.append(first_worker)
        first = await adapter.synthesize('What is my CPU usage?')
        assert first.engine == 'piper' and first.sample_width == 2 and first.channels == 1
        assert len(first.pcm) > 200 and any(first.pcm)
        assert adapter._worker is first_worker and first_worker.returncode is None
        pipe = first_worker.stdin.transport.get_extra_info('pipe')
        assert pipe is not None
        queued = array.array('i', [0])
        fcntl.ioctl(pipe.fileno(), termios.FIONREAD, queued, True)
        assert queued[0] == 0, 'Worker input is not empty before the fixture'
        first_worker.send_signal(signal.SIGSTOP)
        async with asyncio.timeout(2):
            while True:
                state = Path(f'/proc/{first_worker.pid}/status').read_text().split('State:', 1)[1].splitlines()[0].strip()
                if state.startswith('T'):
                    break
                assert first_worker.returncode is None
                await asyncio.sleep(.005)
        pending = asyncio.create_task(adapter.synthesize('What is my CPU usage?'))
        started = time.monotonic()
        async with asyncio.timeout(2):
            while True:
                fcntl.ioctl(pipe.fileno(), termios.FIONREAD, queued, True)
                if queued[0] > 0:
                    break
                assert not pending.done() and first_worker.returncode is None
                await asyncio.sleep(.005)
        assert not pending.done() and adapter._worker is first_worker
        request_bytes = queued[0]
        first_worker.kill()
        await asyncio.wait_for(first_worker.wait(), 3)
        fallback = await asyncio.wait_for(pending, 20)
        assert fallback.engine == 'piper' and len(fallback.pcm) > 200 and any(fallback.pcm)
        assert (fallback.sample_rate, fallback.channels, fallback.sample_width) == (
            first.sample_rate, first.channels, first.sample_width)
        assert adapter._worker is None
        recovery_ms = (time.monotonic() - started) * 1000
        next_audio = await adapter.synthesize('This is a private worker recovery check.')
        replacement = adapter._worker
        assert replacement is not None and replacement.returncode is None
        workers.append(replacement)
        assert replacement.pid != first_worker.pid and next_audio.engine == 'piper'
        assert len(next_audio.pcm) > 200 and any(next_audio.pcm)
        report.update(queued_request_observed_in_kernel_pipe=True, queued_request_bytes=request_bytes,
                      crashed_worker_reaped=True, actual_oneshot_fallback_completed=True,
                      fallback_ms=round(recovery_ms, 3), replacement_worker_verified=True,
                      next_persistent_request_completed=True, worker_synthesis_start_not_claimed=True)
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await adapter.close()
        assert all(worker.returncode is not None for worker in workers)
        assert not {child.pid for child in psutil.Process().children()} - child_pids
        report['owned_persistent_workers_reaped'] = True
        report['no_owned_fallback_child_remaining'] = True
    return report


def run():
    from ev.paths import get_paths

    assert sys.platform.startswith('linux'), 'This fixture observes Linux /proc and pipe state'
    config = json.loads(get_paths().config_file.read_text())['voice']['tts']
    print(json.dumps(asyncio.run(check(config))), flush=True)


if __name__ == '__main__':
    run()
