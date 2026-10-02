"""Explicit local clips with one owned worker and verified private artifacts."""

import asyncio
import hashlib
import json
import math
import os
import re
import stat
import uuid
from pathlib import Path

from .platform import executable
from .process_runner import command, settle

MAXIMUM_BYTES = 64 * 1024 * 1024


class ScreenRecorder:
    def __init__(self, root, python=None):
        self.root = Path(root)
        self.python = python or executable('/usr/bin/python3')
        self.worker = Path(__file__).with_name('recording_worker.py')
        self._lock = asyncio.Lock()
        self.active = False

    async def status(self):
        result = await command([self.python, str(self.worker), '--probe'], timeout=6, maximum=16384)
        try:
            probe = json.loads(result['out']) if result['code'] == 0 else {}
            if not isinstance(probe, dict):
                raise ValueError('Invalid recording probe')
        except (ValueError, TypeError):
            probe = {}
        return {**probe, 'available': probe.get('available') is True, 'active': self.active,
                'reason': str(probe.get('reason') or result['err'] or 'Recording probe unavailable')[:400],
                'consent_required': True, 'continuous_capture': False, 'audio_recorded': False,
                'network_exposed': False, 'retention': 'Completed clips stay until explicitly deleted',
                'maximum_seconds': 120, 'maximum_bytes': MAXIMUM_BYTES,
                'capture_verified': False, 'verified': bool(probe)}

    def _root(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError('Recording directory must be a real private directory')
        os.chmod(self.root, 0o700)
        return self.root.resolve()

    def _path(self, recording_id):
        if not isinstance(recording_id, str) or not re.fullmatch('[0-9a-f]{32}', recording_id):
            raise ValueError('Invalid recording identifier')
        path = self._root() / (recording_id + '.mkv')
        if path.is_symlink():
            raise ValueError('Recording path is a symbolic link')
        return path

    @staticmethod
    def _verify(path, report):
        for field, minimum, maximum in [('frames', 1, 10000), ('width', 1, 1280), ('height', 1, 720)]:
            if type(report.get(field)) is not int or not minimum <= report[field] <= maximum:
                raise ValueError('Recording returned invalid video metadata')
        duration = report.get('duration_seconds')
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 < duration <= 125:
            raise ValueError('Recording returned an invalid video duration')
        if report.get('encoder') not in ('x264enc', 'vp8enc'):
            raise ValueError('Recording returned an unsupported encoder')
        digest = hashlib.sha256()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(descriptor, 'rb') as stream:
            details = os.fstat(stream.fileno())
            size = details.st_size
            if not stat.S_ISREG(details.st_mode) or not 0 < size <= MAXIMUM_BYTES:
                raise ValueError('Recording did not produce a bounded regular video file')
            os.fchmod(stream.fileno(), 0o600)
            if stream.read(4) != b'\x1aE\xdf\xa3':
                raise ValueError('Recording is not a Matroska video')
            stream.seek(0)
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
            named = path.lstat()
            if ((details.st_dev, details.st_ino) != (named.st_dev, named.st_ino)
                or (details.st_size, details.st_mtime_ns) != (after.st_size, after.st_mtime_ns)):
                raise ValueError('Recording changed during verification')
            os.fsync(stream.fileno())
        return {'bytes': size, 'sha256': digest.hexdigest(),
                **{key: report[key] for key in ('frames', 'width', 'height', 'duration_seconds', 'encoder')}}

    async def capture(self, seconds=10):
        if type(seconds) is not int or not 1 <= seconds <= 120:
            raise ValueError('Recording duration must be 1 to 120 seconds')
        if self._lock.locked():
            raise RuntimeError('A screen recording is already active')
        async with self._lock:
            path = None
            self.active = True
            committed = False
            try:
                capabilities = await self.status()
                if not capabilities['available']:
                    raise RuntimeError(capabilities['reason'] + '; ' + ', '.join(capabilities.get('missing', [])))
                recording_id = uuid.uuid4().hex
                final = self._path(recording_id)
                path = final.with_name(recording_id + '.pending.mkv')
                space = os.statvfs(path.parent)
                if space.f_bavail * space.f_frsize < 128 * 1024 * 1024:
                    raise RuntimeError('Screen recording needs at least 128 MiB of free storage')
                result = await command([self.python, str(self.worker), str(path), str(seconds)],
                                       timeout=seconds + 65, maximum=16384)
                try:
                    report = json.loads(result['out'])
                except (ValueError, TypeError):
                    raise RuntimeError(result['err'] or 'Recording worker did not return a result')
                if not isinstance(report, dict) or result['code'] != 0 or report.get('error'):
                    raise RuntimeError(str(report.get('error') if isinstance(report, dict) else '')[:400]
                                       or result['err'] or 'Screen recording failed')
                check = asyncio.create_task(asyncio.to_thread(self._verify, path, report))
                try:
                    await asyncio.wait({check})
                    verified = check.result()
                except asyncio.CancelledError:
                    try:
                        await settle(check)
                    except Exception:
                        pass
                    raise
                if final.exists() or final.is_symlink():
                    raise ValueError('Recording identifier collided with an existing file')
                path.rename(final)
                committed = True
                return {**verified, 'recording_id': recording_id, 'path': str(final), 'verified': True,
                        'audio_recorded': False, 'network_exposed': False,
                        'verification_scope': 'Local container, video metadata, frames and file hash; content not independently inspected'}
            finally:
                self.active = False
                if path is not None and not committed:
                    path.unlink(missing_ok=True)

    def list(self):
        root = self._root()
        files = sorted((p for p in root.glob('*.mkv') if re.fullmatch('[0-9a-f]{32}', p.stem)
                        and not p.is_symlink() and p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
        return {'recordings': [{'recording_id': p.stem, 'path': str(p), 'bytes': p.stat().st_size}
                              for p in files[:20]], 'total': len(files), 'verified': True}

    def delete(self, recording_id):
        path = self._path(recording_id)
        existed = path.is_file()
        path.unlink(missing_ok=True)
        return {'recording_id': recording_id, 'removed': existed, 'verified': not path.exists()}
