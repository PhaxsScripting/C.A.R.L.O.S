#!/usr/bin/env python3
"""Kill disposable model owners and verify exact-server recovery. No host actions."""
import argparse
import asyncio
import ctypes
import json
import os
from pathlib import Path
import select
import signal
import socket
import sys
import tempfile
import time
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
os.environ['PYTHONPATH'] = sys.path[0]
from ev.config import DEFAULT_CONFIG, _merge
from ev.ai.local_llama import LocalLlamaProvider
from ev.voice.stt import WhisperCppAdapter


def adapter(kind, config, runtime):
    if kind == 'llama':
        return LocalLlamaProvider(config, ownership_path=runtime / 'llama-owner.json')
    return WhisperCppAdapter(config, runtime)


def process(model, kind):
    return model._process if kind == 'llama' else model._server_process


async def worker(kind, settings, runtime):
    model = adapter(kind, json.loads(settings.read_text()), runtime)
    try:
        await model.prewarm()
        child = process(model, kind)
        assert child is not None and child.returncode is None
        print(json.dumps({'ready': True, 'server_pid': child.pid}), flush=True)
        await asyncio.Event().wait()
    finally:
        await model.close()


async def check(kind, config, root):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        config['port' if kind == 'llama' else 'server_port'] = probe.getsockname()[1]
    config.update(prewarm=True, persistent_server=True, threads=1, threads_batch=1)
    config['host'] = '127.0.0.1'
    runtime = root / kind
    runtime.mkdir(mode=0o700)
    settings = runtime / 'settings.json'
    settings.write_text(json.dumps(config))
    settings.chmod(0o600)
    owner = await asyncio.create_subprocess_exec(
        sys.executable, str(Path(__file__).resolve()), '--worker', kind,
        '--settings', str(settings), '--runtime', str(runtime),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    candidate = adapter(kind, config, runtime)
    server_handle = None
    server_pid = None
    started = time.monotonic()
    try:
        line = await asyncio.wait_for(owner.stdout.readline(), 45)
        if not line:
            raise RuntimeError('Disposable model owner exited before readiness')
        receipt = json.loads(line)
        assert receipt['ready'] is True
        server_pid = receipt['server_pid']
        server_handle = os.pidfd_open(server_pid)
        try:
            await candidate.prewarm()
        except Exception as error:
            assert 'owned' in str(error).lower() and 'live' in str(error).lower(), str(error)
        else:
            raise AssertionError('A live owner must prevent takeover')
        assert not select.select([server_handle], [], [], 0)[0], 'Live server was signalled'
        owner.kill()
        await owner.wait()
        await candidate.prewarm()
        replacement = process(candidate, kind)
        assert replacement is not None and replacement.pid != server_pid
        assert select.select([server_handle], [], [], 0)[0], 'Recorded orphan did not exit'
        reaped_pid, _ = os.waitpid(server_pid, 0)
        assert reaped_pid == server_pid
        server_pid = None
        print(json.dumps({'backend': kind, 'live_owner_protected': True,
                          'orphan_stopped_and_reaped': True, 'replacement_ready': True,
                          'elapsed_seconds': round(time.monotonic() - started, 3)}), flush=True)
    finally:
        await candidate.close()
        if owner.returncode is None:
            owner.kill()
        await owner.wait()
        if server_handle is not None:
            if not select.select([server_handle], [], [], 0)[0]:
                signal.pidfd_send_signal(server_handle, signal.SIGKILL)
            os.close(server_handle)
        if server_pid is not None:
            os.waitpid(server_pid, 0)
        # A readiness timeout can kill the owner before it sends its receipt.
        for child in psutil.Process().children():
            handle = None
            try:
                handle = os.pidfd_open(child.pid)
                if child.cmdline() == candidate._server_command():
                    signal.pidfd_send_signal(handle, signal.SIGKILL)
                    await asyncio.to_thread(os.waitpid, child.pid, 0)
            except (ProcessLookupError, ChildProcessError, psutil.NoSuchProcess):
                pass
            finally:
                if handle is not None:
                    os.close(handle)


async def main(config_path):
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise RuntimeError('Linux pidfd support is required')
    # Adopt only this fixture's descendants so its killed-owner children can be reaped.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0):
        raise OSError(ctypes.get_errno(), 'Cannot become a child subreaper')
    local = json.loads(config_path.read_text())
    configs = {
        'llama': _merge(DEFAULT_CONFIG['providers']['local_llama'],
                        local.get('providers', {}).get('local_llama', {})),
        'whisper': _merge(DEFAULT_CONFIG['voice']['stt'], local.get('voice', {}).get('stt', {})),
    }
    del local
    with tempfile.TemporaryDirectory(prefix='carlos-owner-recovery-') as temp:
        for kind, config in configs.items():
            await check(kind, config, Path(temp))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--config', type=Path, default=Path.home() / '.config/ev/config.json')
    parser.add_argument('--worker', choices=('llama', 'whisper'), help=argparse.SUPPRESS)
    parser.add_argument('--settings', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--runtime', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        asyncio.run(worker(args.worker, args.settings, args.runtime))
    elif args.run:
        asyncio.run(main(args.config))
    else:
        parser.error('--run is required; this check kills only its disposable owner processes')
