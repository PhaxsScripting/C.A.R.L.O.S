from __future__ import annotations

import tempfile
import unittest
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "core"))

from ev.coding_agent import CodingAgentGateway


class CodingAgentGatewayTests(unittest.TestCase):
    @staticmethod
    def _repository(root: Path) -> None:
        subprocess.run(["/usr/bin/git", "init", "-q", str(root)], check=True)
        (root / "README.md").write_text("# test\n", encoding="utf-8")
        subprocess.run(["/usr/bin/git", "-C", str(root), "add", "README.md"], check=True)
        subprocess.run(
            [
                "/usr/bin/git",
                "-C",
                str(root),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@localhost",
                "commit",
                "-qm",
                "initial",
            ],
            check=True,
        )

    def test_proposal_never_executes_or_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
            before = {item.relative_to(root) for item in root.rglob("*")}
            proposal = CodingAgentGateway([str(root)]).propose("Fix the failing test", str(root))
            self.assertFalse(proposal["executed"])
            self.assertTrue(proposal["approval_required"])
            self.assertEqual(proposal["permission_class"], "HIGH")
            self.assertEqual({item.relative_to(root) for item in root.rglob("*")}, before)

    def test_rejects_projects_outside_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as outside:
            with self.assertRaises(ValueError):
                CodingAgentGateway([allowed]).propose("Fix it", outside)

    def test_persisted_diagnostics_redact_common_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "project"
            root.mkdir()
            self._repository(root)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
            state = base / "state"
            gateway = CodingAgentGateway([str(base)], state)
            proposal = gateway.propose(
                "Inspect a safe failure", str(root), "token=super-secret sk-abcdefghijklmnop"
            )
            stored = (state / proposal["proposal_id"] / "task.json").read_text(encoding="utf-8")
            self.assertNotIn("super-secret", stored)
            self.assertNotIn("sk-abcdefghijklmnop", stored)
            self.assertIn("[REDACTED]", stored)

    def test_standalone_cli_login_status_on_stderr_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = Path(temporary) / "codex"
            fake.write_text(
                """#!/bin/sh
if [ "$1" = "--version" ]; then
    printf '%s\\n' 'codex-cli test'
elif [ "$1" = "login" ] && [ "$2" = "status" ]; then
    printf '%s\\n' 'Logged in using ChatGPT' >&2
else
    exit 2
fi
""",
                encoding="utf-8",
            )
            fake.chmod(0o700)
            gateway = CodingAgentGateway([temporary])
            with patch.object(CodingAgentGateway, "_codex_executable", return_value=str(fake)):
                status = gateway.status(refresh=True)
            self.assertTrue(status["available"])
            self.assertTrue(status["authenticated"])
            self.assertEqual(status["reason"], "Codex CLI and saved login are ready")

    def test_approved_execution_uses_worktree_and_commits_only_validated_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "project"
            root.mkdir()
            self._repository(root)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
            fake = base / "codex"
            fake.write_text(
                """#!/usr/bin/python3
import json
import pathlib
import sys
if '--version' in sys.argv:
    print('codex-cli test')
elif len(sys.argv) > 2 and sys.argv[1:3] == ['login', 'status']:
    print('Logged in using test')
else:
    worktree = pathlib.Path(sys.argv[sys.argv.index('-C') + 1])
    (worktree / 'generated.txt').write_text('validated\\n', encoding='utf-8')
    print(json.dumps({'type': 'thread.started', 'thread_id': 'thread-test'}))
    print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Implemented.'}}))
    print(json.dumps({'type': 'turn.completed'}))
""",
                encoding="utf-8",
            )
            fake.chmod(0o700)
            events = []
            gateway = CodingAgentGateway([str(base)], base / "state", lambda kind,payload,correlation:events.append((kind,payload)))
            with patch.object(CodingAgentGateway, "_codex_executable", return_value=str(fake)):
                proposal = gateway.propose("Add generated.txt", str(root))
                result = gateway.execute(proposal["proposal_id"], 60)
            self.assertEqual(result["status"], "VALIDATED_AWAITING_DEPLOYMENT_REVIEW")
            self.assertEqual(result["change_review"]["changed_files"], ["generated.txt"])
            self.assertTrue(all(item["passed"] for item in result["tests"]))
            self.assertTrue(result["commit_id"])
            self.assertFalse((root / "generated.txt").exists())
            self.assertTrue((Path(result["worktree"]) / "generated.txt").is_file())
            waiting = [payload for kind,payload in events if kind == 'coding.waiting']
            self.assertEqual([item['phase'] for item in waiting], ['REVIEW_PROPOSAL','REVIEW_DEPLOYMENT'])
            self.assertTrue(all(set(item) == {'proposal_id','phase','observed_at','approval_required'} for item in waiting))

    def test_running_job_reports_safe_progress_and_cancels_without_commit(self):
        import concurrent.futures
        import threading
        import json

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "project"
            root.mkdir()
            self._repository(root)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
            fake = base / "codex"
            fake.write_text(
                "#!/usr/bin/python3\nimport sys,json,time\nif '--version' in sys.argv: print('fixture')\nelif sys.argv[1:3]==['login','status']: print('Logged in using test')\nelse:\n print(json.dumps({'type':'item.completed','item':{'type':'reasoning','text':'HIDDEN_CANARY'}}),flush=True)\n print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Working token=private-canary'}}),flush=True)\n time.sleep(30)\n"
            )
            fake.chmod(0o700)
            emitted = []
            started = threading.Event()

            def sink(kind, payload, correlation):
                emitted.append((kind, payload))
                if kind == "coding.progress":
                    started.set()

            gateway = CodingAgentGateway([str(base)], base / "state", sink)
            with patch.object(CodingAgentGateway, "_codex_executable", return_value=str(fake)):
                proposal = gateway.propose("Fixture cancellation", str(root))
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    task = pool.submit(gateway.execute, proposal["proposal_id"], 60)
                    try:
                        self.assertTrue(started.wait(5))
                        self.assertTrue(gateway.cancel(proposal["proposal_id"])["cancel_requested"])
                        result = task.result(timeout=5)
                    finally:
                        gateway.cancel_all()
            self.assertEqual(result["status"], "CANCELLED")
            self.assertNotIn("commit_id", result)
            self.assertNotIn("HIDDEN_CANARY", json.dumps(emitted))
            self.assertNotIn("private-canary", json.dumps(emitted))
            self.assertFalse(gateway.cancel(proposal["proposal_id"])["cancel_requested"])

    def test_configured_executable_is_used_without_installing_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "bundled-codex"
            binary.write_text("#!/bin/sh\nif [ \"$1\" = --version ]; then echo codex-test; else echo 'Logged in using test'; fi\n")
            binary.chmod(0o700)
            gateway = CodingAgentGateway([], executable=str(binary))
            with patch.object(CodingAgentGateway, "_codex_executable", side_effect=AssertionError("unexpected discovery")):
                status = gateway.status()
            self.assertTrue(status["available"])
            self.assertEqual(status["executable"], str(binary))
            binary.unlink()
            self.assertFalse(gateway.status(refresh=True)["available"])

    def test_nested_release_validation_runs_tests_with_core_imports(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            self._repository(project)
            core = project / "carlos/core/ev"
            core.mkdir(parents=True)
            (core / "__init__.py").write_text("VALUE = 42\n")
            tests = project / "carlos/tests"
            tests.mkdir()
            test = tests / "test_fixture.py"
            test.write_text("import unittest\nfrom ev import VALUE\nclass Check(unittest.TestCase):\n def test_value(self): self.assertEqual(VALUE, 42)\n")
            gateway = CodingAgentGateway([str(project)])
            results = gateway._validate(project)
            self.assertEqual([r["name"] for r in results], ["git diff --check", "python compileall", "Python unit suite"])
            self.assertTrue(all(r["passed"] for r in results), results)
            test.write_text(test.read_text().replace("VALUE, 42", "VALUE, 999"))
            self.assertFalse(gateway._validate(project)[-1]["passed"])

    def test_review_commit_uses_project_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
            for key, value in [("user.name", "Fixture Owner"), ("user.email", "owner@example.invalid")]:
                subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
            (root / "README.md").write_text("fixed\n")
            result = CodingAgentGateway([str(root)])._commit_result(root, "a"*32)
            self.assertEqual(result["returncode"], 0, result)
            author = subprocess.check_output(["git", "-C", str(root), "show", "-s", "--format=%an <%ae>"], text=True).strip()
            self.assertEqual(author, "Fixture Owner <owner@example.invalid>")


if __name__ == "__main__":
    unittest.main()
