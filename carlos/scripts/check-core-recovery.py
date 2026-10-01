#!/usr/bin/env python3
"""Kill a disposable core, then verify receipts and local IPC after restart."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))


async def call(socket_path, kind, payload=None, identifier=None):
    async with asyncio.timeout(8):
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        try:
            await reader.readline()
            identifier = identifier or uuid.uuid4().hex
            writer.write((json.dumps({'type': kind, 'id': identifier,
                                      'payload': payload or {}}) + '\n').encode())
            await writer.drain()
            while line := await reader.readline():
                reply = json.loads(line)
                if reply.get('id') == identifier:
                    if reply['type'] == 'error':
                        raise RuntimeError(reply['payload'])
                    return reply['payload']
            raise RuntimeError('Core disconnected before replying')
        finally:
            writer.close()
            await writer.wait_closed()


async def ready(process, socket_path):
    started = time.monotonic()
    async with asyncio.timeout(15):
        while process.poll() is None:
            try:
                health = await call(socket_path, 'health')
                if health.get('ok') and (await call(socket_path, 'snapshot'))['pid'] == process.pid:
                    return round((time.monotonic() - started) * 1000, 1)
            except (OSError, RuntimeError):
                pass
            await asyncio.sleep(.05)
    raise RuntimeError('Disposable core exited before readiness')


async def fixture():
    from ev.task_journal import TaskJournal

    if ('CARLOS_RECOVERY_PARENT_BUS' not in os.environ
            or os.environ.get('DBUS_SESSION_BUS_ADDRESS') == os.environ['CARLOS_RECOVERY_PARENT_BUS']):
        raise RuntimeError('This acceptance test needs a separate session bus')
    with tempfile.TemporaryDirectory(prefix='carlos-core-recovery-') as directory:
        root = Path(directory)
        environment = os.environ.copy()
        for key, subdirectory in [('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'),
                                  ('XDG_STATE_HOME', 'state'), ('XDG_CACHE_HOME', 'cache'),
                                  ('XDG_RUNTIME_DIR', 'run')]:
            (root / subdirectory).mkdir(mode=0o700)
            environment[key] = str(root / subdirectory)
        environment.update(PYTHONPATH=str(ROOT / 'core'), EV_DBUS_NAME='com.ev.Core',
                           DISPLAY='', WAYLAND_DISPLAY='', XDG_CURRENT_DESKTOP='',
                           DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(root / 'no-system-bus'),
                           PULSE_SERVER='unix:' + str(root / 'no-audio-server'))
        config = root / 'config/ev'
        config.mkdir(mode=0o700)
        (config / 'config.json').write_text(json.dumps({
            'providers': {'active': 'offline'},
            'carlos': {'privacy_mode': 'DO NOT LISTEN'},
            'assistant': {'speak_responses': False, 'wake_enabled': False},
            'accessibility': {'auto_enable_session': False},
            'security': {'allowed_roots': [str(root)]},
            'voice': {'wake': {'enabled': False}, 'vad': {'neural_enabled': False}},
            'plugins': {'enabled': []},
        }))
        socket_path = root / 'run/ev/ev.sock'
        processes, logs = [], []

        def start(label):
            log = (root / (label + '.log')).open('w')
            logs.append(log)
            process = subprocess.Popen([sys.executable, '-m', 'ev'], env=environment,
                                       stdout=log, stderr=log)
            processes.append(process)
            return process

        try:
            original = start('original')
            startup_ms = await ready(original, socket_path)
            duplicate = start('duplicate')
            code = await asyncio.wait_for(asyncio.to_thread(duplicate.wait), 8)
            if code != 2 or (await call(socket_path, 'snapshot'))['pid'] != original.pid:
                raise RuntimeError('Duplicate core changed the active owner')

            task_id = uuid.uuid4().hex
            journal = TaskJournal(root / 'data/ev/agent-tasks.db')
            journal.begin(task_id, 'Disposable recovery acceptance task')
            marker = root / 'receipt.txt'
            result = await call(socket_path, 'tool.call', {
                'name': 'files.text.create',
                'arguments': {'path': str(marker), 'content': 'created once by fixture\n'},
            }, task_id)
            if result['status'] != 'completed' or not marker.is_file():
                raise RuntimeError('Fixture file action did not complete')
            before = marker.stat()
            before_hash = hashlib.sha256(marker.read_bytes()).hexdigest()
            uncertain = journal.start_step(task_id, 'agent.wait_for', {'fixture': True})
            journal.close()

            reader, writer = await asyncio.open_unix_connection(str(socket_path))
            await reader.readline()
            original.kill()
            await asyncio.wait_for(asyncio.to_thread(original.wait), 5)
            if await asyncio.wait_for(reader.read(), 5):
                raise RuntimeError('Unexpected stream contents after killed core')
            writer.close()
            await writer.wait_closed()

            replacement = start('replacement')
            restart_ms = await ready(replacement, socket_path)
            recovered = await call(socket_path, 'agent.tasks.get', {'id': task_id})
            task = recovered['task']
            if task['status'] != 'INTERRUPTED':
                raise RuntimeError('Killed task was not marked interrupted')
            steps = task['steps']
            if len(steps) != 2 or steps[0]['status'] != 'COMPLETED':
                raise RuntimeError('Completed receipt changed during recovery')
            if steps[1]['id'] != uncertain or steps[1]['status'] != 'INTERRUPTED_UNCERTAIN':
                raise RuntimeError('Unfinished step lost its uncertainty')
            answer = await call(socket_path, 'command.submit', {'text': 'What is my CPU usage?'})
            if answer['status'] != 'completed':
                raise RuntimeError('Local request failed after recovery')
            await asyncio.sleep(.3)
            after = marker.stat()
            if ((before.st_ino, before.st_mtime_ns) != (after.st_ino, after.st_mtime_ns)
                    or hashlib.sha256(marker.read_bytes()).hexdigest() != before_hash):
                raise RuntimeError('Recovery replayed or changed the completed file action')
            voice = (await call(socket_path, 'snapshot'))['voice']
            if voice.get('wake_active') or voice.get('capturing'):
                raise RuntimeError('Fixture unexpectedly enabled audio input')
            await call(socket_path, 'core.stop')
            await asyncio.wait_for(asyncio.to_thread(replacement.wait), 15)
            print(json.dumps({
                'session': 'isolated D-Bus, storage, audio/display endpoints',
                'startup_ms': startup_ms, 'restart_to_ipc_ms': restart_ms,
                'duplicate_owner_refused': True, 'old_connection_closed': True,
                'completed_receipt_preserved': True,
                'unfinished_step': 'INTERRUPTED_UNCERTAIN',
                'file_readback': 'same inode, mtime and SHA-256; no replay',
                'local_request_after_restart': 'completed',
                'restart': 'explicit fixture restart; automatic HUD activation is separate',
                'unfinished_step_source': 'seeded running receipt, not an actual interrupted tool',
            }, indent=2))
        except BaseException:
            for log in logs:
                log.flush()
            for path in root.glob('*.log'):
                print(path.name + ':\n' + path.read_text()[-3000:], file=sys.stderr)
            raise
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                await asyncio.to_thread(process.wait)
            for log in logs:
                log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--fixture', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.fixture:
        asyncio.run(fixture())
    elif args.run:
        environment = os.environ.copy()
        environment['CARLOS_RECOVERY_PARENT_BUS'] = environment.get('DBUS_SESSION_BUS_ADDRESS', '')
        subprocess.run(['dbus-run-session', '--', sys.executable, str(Path(__file__).resolve()),
                        '--fixture'], env=environment, check=True, timeout=90)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
