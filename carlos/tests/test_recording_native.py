import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ev.recording import ScreenRecorder
from ev import recording_worker


class NativeRecordingTests(unittest.TestCase):
    def test_native_encoder_produces_a_decodable_bounded_clip_from_test_pattern(self):
        python = Path('/usr/bin/python3')
        if not python.is_file() or not shutil.which('ffprobe'):
            self.skipTest('Native Python or ffprobe unavailable')
        script = '''
import importlib.util, json, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('worker', sys.argv[1])
worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
try:
    _, GLib, Gst = worker.native_stack()
except (ImportError, ValueError):
    raise SystemExit(77)
if not worker.codec(Gst) or any(not Gst.ElementFactory.find(name) for name in
    ('videotestsrc', 'videoconvert', 'videoscale', 'videorate', 'matroskamux', 'filesink')):
    raise SystemExit(77)
source = Gst.ElementFactory.make('videotestsrc'); source.set_property('is-live', True)
result = worker.record_pipeline(source, Path(sys.argv[2]), 1, worker.codec(Gst), Gst, GLib)
result['registry_path'] = os.environ['GST_REGISTRY_1_0']
print(json.dumps(result))
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pattern.mkv'
            result = subprocess.run([str(python), '-c', script, recording_worker.__file__, str(path)],
                                    capture_output=True, text=True, timeout=15,
                                    env={**os.environ, 'XDG_CACHE_HOME': str(Path(directory) / 'cache')})
            if result.returncode == 77:
                self.skipTest('Native GI or recording encoder unavailable')
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(Path(report['registry_path']).parent, Path(directory) / 'cache/ev/recording')
            self.assertEqual((report['width'], report['height']), (1280, 720))
            self.assertGreaterEqual(report['frames'], 15)
            self.assertGreaterEqual(report['duration_seconds'], 1)
            self.assertLess(report['duration_seconds'], 2)
            verified = ScreenRecorder._verify(path, report)
            self.assertGreater(verified['bytes'], 1000)
            decoded = subprocess.run([shutil.which('ffprobe'), '-v', 'error', '-count_frames',
                '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_frames', '-of', 'json', str(path)],
                capture_output=True, text=True, timeout=5)
            self.assertEqual(decoded.returncode, 0, decoded.stderr)
            self.assertGreaterEqual(int(json.loads(decoded.stdout)['streams'][0]['nb_read_frames']), 15)
            self.assertEqual(int(json.loads(decoded.stdout)['streams'][0]['nb_read_frames']), report['frames'])
