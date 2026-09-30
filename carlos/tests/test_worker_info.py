import unittest
from types import SimpleNamespace
from unittest.mock import patch
from ev.voice.worker_info import runtime_versions, safe_versions, worker_report


class WorkerInfoTests(unittest.TestCase):
    def test_metadata_contains_only_versions_and_known_packages(self):
        self.assertEqual(safe_versions({"python":"3.12.9", "onnxruntime":"1.22.1", "piper-tts":"/private/file", "token":"canary", "numpy":True}),
                         {"python":"3.12.9", "onnxruntime":"1.22.1"})
        for value in (None, [], "secret"):
            self.assertEqual(safe_versions(value), {})

    def test_worker_runtime_is_inspected_inside_its_environment(self):
        with patch("ev.voice.worker_info.version", return_value="9.8.7") as read:
            result=runtime_versions("onnxruntime", "unrelated")
        self.assertEqual(result["onnxruntime"], "9.8.7")
        read.assert_called_once_with("onnxruntime")

    def test_stopped_worker_keeps_evidence_without_claiming_it_is_running(self):
        worker=SimpleNamespace(worker_versions={"python":"3.12.9"})
        for process in (None, SimpleNamespace(returncode=0)):
            result=worker_report(worker,process)
            self.assertFalse(result["running"])
            self.assertEqual(result["versions"], {"python":"3.12.9"})
        self.assertTrue(worker_report(worker,SimpleNamespace(returncode=None))["running"])
