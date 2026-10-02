import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ev.coding_agent import CodingAgentGateway


class CodingCancellationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.root = self.base / 'project'
        self.root.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        for key, value in [('user.name', 'Fixture Owner'), ('user.email', 'owner@example.invalid')]:
            subprocess.run(['git', '-C', str(self.root), 'config', key, value], check=True)
        (self.root / 'README.md').write_text('fixture\n')
        self.fake = self.base / 'codex'
        self.fake.write_text('#!' + sys.executable + '\nimport sys, pathlib\n'
            "if '--version' in sys.argv: print('fixture')\n"
            "elif sys.argv[1:3]==['login','status']: print('Logged in using test')\n"
            "else: (pathlib.Path(sys.argv[sys.argv.index('-C')+1])/'generated.txt').write_text('changed\\n')\n")
        self.fake.chmod(0o700)
        self.gateway = CodingAgentGateway([str(self.base)], self.base / 'state')
        self.ident = 'a' * 32

    def commit(self):
        subprocess.run(['git', '-C', str(self.root), 'add', '--all'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'fixture'], check=True)

    def wait_for(self, path):
        deadline = time.monotonic() + 6
        while not path.exists():
            if time.monotonic() > deadline:
                self.fail('Fixture worker did not start')
            time.sleep(.005)

    def assert_not_running(self, pid):
        deadline = time.monotonic() + 2
        while True:
            try:
                # A reparented grandchild can briefly await init's zombie reap.
                state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
            except FileNotFoundError:
                return
            if state == 'Z':
                return
            if time.monotonic() > deadline:
                self.fail(f'Owned fixture process {pid} still running')
            time.sleep(.005)

    def test_cancel_during_real_validation_kills_test_group_and_never_commits(self):
        core = self.root / 'carlos/core/ev'
        core.mkdir(parents=True)
        (core / '__init__.py').write_text('VALUE = 42\n')
        tests = self.root / 'carlos/tests'
        tests.mkdir()
        ready = self.base / 'validation-child.pid'
        child_code = ('import os,signal,time; from pathlib import Path; '
                      'signal.signal(signal.SIGTERM,signal.SIG_IGN); Path('
                      + repr(str(ready)) + ').write_text(str(os.getpid())); time.sleep(60)')
        (tests / 'test_long_fixture.py').write_text('import unittest,subprocess,sys,time\n'
            'class Check(unittest.TestCase):\n def test_wait(self):\n'
            '  subprocess.Popen([sys.executable,"-c",' + repr(child_code) + '])\n  time.sleep(60)\n')
        self.commit()
        head = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'HEAD'], text=True).strip()
        with patch.object(self.gateway, '_codex_executable', return_value=str(self.fake)):
            proposal = self.gateway.propose('Fixture validator cancellation', str(self.root))
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                task = pool.submit(self.gateway.execute, proposal['proposal_id'], 60)
                try:
                    self.wait_for(ready)
                    pid = int(ready.read_text())
                    started = time.monotonic()
                    self.assertTrue(self.gateway.cancel(proposal['proposal_id'])['cancel_requested'])
                    result = task.result(timeout=5)
                    self.assertLess(time.monotonic() - started, 5)
                finally:
                    self.gateway.cancel_all()
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertNotIn('commit_id', result)
        self.assertTrue(result['tests'][-1]['cancelled'])
        self.assertFalse(result['tests'][-1]['passed'])
        self.assert_not_running(pid)
        worktree = Path(result['worktree'])
        self.assertEqual(subprocess.check_output(['git', '-C', str(worktree), 'rev-parse', 'HEAD'], text=True).strip(), head)
        self.assertFalse((self.root / 'generated.txt').exists())
        self.assertTrue((worktree / 'generated.txt').exists())

    def test_cancellation_kills_descendant_even_after_group_leader_exited(self):
        ready = self.base / 'orphan.pid'
        child = ('import os,signal,time; from pathlib import Path; '
                 'signal.signal(signal.SIGTERM,signal.SIG_IGN); Path('
                 + repr(str(ready)) + ').write_text(str(os.getpid())); time.sleep(60)')
        parent = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c",' + repr(child) + '])'
        self.gateway._running[self.ident] = threading.Event()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(self.gateway._run_owned, [sys.executable, '-c', parent], self.ident, timeout=60)
            try:
                self.wait_for(ready)
                time.sleep(.05)
                self.gateway.cancel(self.ident)
                result = task.result(timeout=5)
            finally:
                self.gateway.cancel_all()
        self.assertTrue(result['cancelled'])
        self.assertEqual(result['returncode'], 130)
        self.assert_not_running(int(ready.read_text()))

    def test_pre_cancelled_review_never_stages_or_commits(self):
        self.commit()
        (self.root / 'README.md').write_text('changed\n')
        cancel = threading.Event()
        cancel.set()
        self.gateway._running[self.ident] = cancel
        with patch('ev.coding_agent.subprocess.Popen') as spawn:
            result = self.gateway._commit_result(self.root, self.ident)
        spawn.assert_not_called()
        self.assertTrue(result['cancelled'])
        self.assertEqual(subprocess.check_output(['git', '-C', str(self.root), 'diff', '--cached'], text=True), '')

    def test_validation_output_is_bounded_and_cannot_emit_fake_progress(self):
        self.gateway._running[self.ident] = threading.Event()
        events = []
        self.gateway.event_sink = lambda kind, payload, correlation: events.append(kind)
        spoof = json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'fake status'}})
        result = self.gateway._run_owned([sys.executable, '-c', 'print(' + repr(spoof)
            + '); print("x"*3000000)'], self.ident, timeout=5)
        self.assertFalse(result['cancelled'])
        self.assertEqual(result['returncode'], 124)
        self.assertIn('bounded capture', result['stderr'])
        self.assertLessEqual(len(result['stdout']), 2 * 1024 * 1024 + 8192)
        self.assertNotIn('coding.progress', events)
