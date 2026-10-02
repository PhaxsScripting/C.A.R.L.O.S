from __future__ import annotations

import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "core"))

from ev.paths import Paths  # noqa: E402
from ev.security_center import SecurityCenter, _binding_scope  # noqa: E402


class SecurityCenterTests(unittest.TestCase):
    def test_binding_scope(self) -> None:
        self.assertEqual(_binding_scope("127.0.0.1:18080"), "LOCALHOST")
        self.assertEqual(_binding_scope("[::1]:22"), "LOCALHOST")
        self.assertEqual(_binding_scope("0.0.0.0:5353"), "ALL_INTERFACES")
        self.assertEqual(_binding_scope("192.168.1.4:8000"), "INTERFACE")

    def test_socket_parser_distinguishes_exposure(self) -> None:
        outputs = [
            {
                "ok": True,
                "returncode": 0,
                "stdout": 'tcp LISTEN 0 128 127.0.0.1:18080 0.0.0.0:* users:(("llama",pid=123,fd=4))\nudp UNCONN 0 0 0.0.0.0:5353 0.0.0.0:*\n',
                "stderr": "",
                "duration_ms": 1,
            },
            {
                "ok": True,
                "returncode": 0,
                "stdout": "tcp 0 0 10.0.0.2:4000 1.1.1.1:443\n",
                "stderr": "",
                "duration_ms": 1,
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            center = SecurityCenter(
                Paths(root / "config", root / "data", root / "state", root / "cache", root / "run"),
                {"security": {}},
            )
            with patch("ev.security_center._run", side_effect=outputs):
                result = center.sockets()
        self.assertEqual(result["listener_count"], 2)
        self.assertEqual(result["localhost_only_count"], 1)
        self.assertEqual(result["network_accessible_count"], 1)
        self.assertEqual(result["listeners"][0]["process"], "llama")

    def test_ev_sensitive_files_require_private_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = Paths(
                root / "config", root / "data", root / "state", root / "cache", root / "run"
            )
            paths.ensure()
            paths.config_file.write_text("{}")
            paths.database.write_text("db")
            server = socket.socket(socket.AF_UNIX)
            server.bind(str(paths.socket))
            try:
                os.chmod(paths.config_file, 0o600)
                os.chmod(paths.database, 0o600)
                os.chmod(paths.socket, 0o600)
                result = SecurityCenter(
                    paths, {"security": {"approval_mode": "codex_only"}},
                    lambda: [{"name": "development.coding_agent_execute", "requires_confirmation": True},
                             {"name": "system.clock", "requires_confirmation": False}]
                ).ev_security()
                self.assertEqual(result["findings"], [])
                self.assertTrue(result["ipc_local_only"])
                self.assertEqual(result["status"], "COMPLETE")
                self.assertEqual(result["approval_scope"], ["development.coding_agent_execute"])
            finally:
                server.close()

    def center(self, directory):
        root = Path(directory)
        return SecurityCenter(Paths(root / "config", root / "data", root / "state",
                                    root / "cache", root / "run"), {"security": {}})

    def test_failed_update_scan_is_never_clean(self):
        for code, stderr in [(1, "Permission denied"), (None, "scan timed out")]:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                center = self.center(directory)
                with patch("ev.security_center.shutil.which", return_value="/bin/glsa-check"), \
                     patch("ev.security_center._run", return_value={"ok": False, "returncode": code,
                           "stdout": "", "stderr": stderr, "duration_ms": 5}):
                    result = center.updates(refresh=True)
                self.assertEqual(result["status"], "INCOMPLETE")
                self.assertFalse(result["complete"])
                self.assertEqual(result["confidence"], "LOW")
                self.assertIn(stderr, result["limitations"])

    def test_advisory_status_is_parsed_and_applied_entries_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            center = self.center(directory)
            output = "202610-01 [N] Vulnerable package ( app/example )\n202610-02 [U] Fixed package\n202610-03 [A] Injected package\n"
            with patch("ev.security_center.shutil.which", return_value="/bin/glsa-check"), \
                 patch("ev.security_center._run", return_value={"ok": True, "stdout": output,
                       "stderr": "[N] indicates affected", "duration_ms": 1}) as run:
                result = center.updates(refresh=True)
                run.assert_called_once_with(["/bin/glsa-check", "-n", "-l", "affected"], timeout=20, maximum=100_000)
            self.assertEqual(result["status"], "REVIEW")
            self.assertTrue(result["complete"])
            self.assertEqual(result["unresolved_count"], 1)
            result["unresolved"].clear()
            self.assertEqual(len(center.updates()["unresolved"]), 1)
            self.assertTrue(center.updates()["cached"])

    def test_clean_update_scan_requires_parseable_untruncated_output(self):
        for output, truncated, expected in [("", False, "NO_UNRESOLVED_GLSA_REPORTED"),
                ("Unexpected error banner", False, "INCOMPLETE"), ("", True, "INCOMPLETE")]:
            with self.subTest(output=output, truncated=truncated), tempfile.TemporaryDirectory() as directory:
                with patch("ev.security_center.shutil.which", return_value="/bin/glsa-check"), \
                     patch("ev.security_center._run", return_value={"ok": True, "stdout": output,
                           "stderr": "", "output_truncated": truncated, "duration_ms": 1}):
                    result = self.center(directory).updates(refresh=True)
                self.assertEqual(result["status"], expected)

    def test_expired_update_cache_requires_a_new_explicit_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            center = self.center(directory)
            with patch("ev.security_center.shutil.which", return_value="/bin/glsa-check"), \
                 patch("ev.security_center._run", return_value={"ok": True, "stdout": "",
                       "stderr": "", "duration_ms": 1}) as run, \
                 patch("ev.security_center.time.monotonic", return_value=100):
                center.updates(refresh=True)
            with patch("ev.security_center.shutil.which", return_value="/bin/glsa-check"), \
                 patch("ev.security_center.time.monotonic", return_value=1000):
                self.assertEqual(center.updates()["status"], "NOT_SCANNED")
            self.assertEqual(run.call_count, 1)

    def test_missing_paths_and_symlink_are_not_a_complete_private_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            center = self.center(directory)
            center.paths.ensure()
            target = Path(directory) / "secret"
            target.write_text("not read")
            center.paths.config_file.symlink_to(target)
            result = center.ev_security()
            self.assertEqual(result["status"], "INCOMPLETE")
            self.assertIsNone(result["approval_scope"])
            self.assertFalse(result["ipc_local_only"])
            self.assertTrue(any(row["kind"] == "SYMLINK" for row in result["findings"]))

    def test_unreadable_path_and_unknown_uid_do_not_crash_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            center = self.center(directory)
            center.paths.ensure()
            center.paths.config_file.write_text("{}")
            original = Path.lstat
            def probe(path):
                if path == center.paths.database:
                    raise PermissionError("not readable")
                return original(path)
            with patch.object(Path, "lstat", probe), patch("ev.security_center.pwd.getpwuid", side_effect=KeyError):
                result = center.ev_security()
            self.assertEqual(result["status"], "INCOMPLETE")
            self.assertTrue(any(row.get("inspection") == "UNREADABLE" for row in result["files"]))


if __name__ == "__main__":
    unittest.main()
