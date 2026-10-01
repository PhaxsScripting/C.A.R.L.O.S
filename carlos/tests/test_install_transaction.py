import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]


class InstallTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.project = self.root / 'project'
        self.commands = self.root / 'commands'
        self.commands.mkdir()
        self.app = self.home / '.local/share/ev/app'
        self.bin = self.home / '.local/bin'
        self.events = self.root / 'events'
        self.active = self.root / 'active'
        self.active.touch()
        for folder in ('scripts', 'packaging', 'core', 'assets/voice', 'assets/kwin', 'build/ui', 'tests'):
            (self.project / folder).mkdir(parents=True)
        for name in ('install-user.sh', 'rollback-user.py'):
            shutil.copy2(PROJECT / 'scripts' / name, self.project / 'scripts' / name)
        shutil.copytree(PROJECT / 'packaging', self.project / 'packaging', dirs_exist_ok=True)
        self.app.mkdir(parents=True)
        (self.app / 'core').mkdir()
        (self.app / 'core/version').write_text('old')
        (self.project / 'core/version').write_text('new')
        (self.project / 'tests/test_fixture.py').write_text('import unittest\nclass Fixture(unittest.TestCase):\n def test_package(self): self.assertTrue(True)\n')
        self.bin.mkdir(parents=True)
        self.shell(self.project / 'build/ui/ev-ui', 'exit 0')
        self.shell(self.project / 'build/ui/ev-pet', 'exit 0')
        ctl = '''version=$(cat "$HOME/.local/share/ev/app/core/version")
case "$1" in
health) test -f "$FIXTURE_ACTIVE" && { test "$version" = old || test "${FIXTURE_FAILURE:-}" != health; } ;;
stop) printf 'stop %s\\n' "$version" >> "$FIXTURE_EVENTS"; test "${FIXTURE_FAILURE:-}" = stuck || rm -f "$FIXTURE_ACTIVE" ;;
esac'''
        activate = '''version=$(cat "$HOME/.local/share/ev/app/core/version")
printf 'activate %s\\n' "$version" >> "$FIXTURE_EVENTS"
touch "$FIXTURE_ACTIVE"'''
        for name in ('ev-core', 'evctl', 'carlosctl', 'ev-ui', 'ev-activate', 'ev-panel-state', 'carlos-pet'):
            body = ctl if name == 'evctl' else activate if name == 'ev-activate' else 'exit 0'
            self.shell(self.project / 'scripts' / name, body)
            self.shell(self.bin / name, body)
        (self.project / 'scripts/prepare-runtime.py').write_text('''import os,sys
from pathlib import Path
if os.environ.get('FIXTURE_FAILURE') == 'runtime': raise SystemExit(9)
p=Path(sys.argv[1])/'bin/python'
p.parent.mkdir(parents=True)
p.write_text('#!/bin/sh\\nexit 0\\n')
p.chmod(0o755)
''')
        for name in ('sleep', 'update-desktop-database'):
            self.shell(self.commands / name, 'exit 0')
        self.shell(self.commands / 'gdbus', '''printf 'dbus\\n' >> "$FIXTURE_EVENTS"
if test "${FIXTURE_FAILURE:-}" = bus; then exit 7; fi
if test -f "$FIXTURE_ACTIVE"; then printf '(true,)\\n'; else printf '(false,)\\n'; fi''')
        for command, stage, ending in [('install', 'copy', '/.local/bin/ev-ui'), ('cp', 'backup', '/files/evctl')]:
            real = shutil.which(command)
            self.shell(self.commands / command, f'''for arg do last=$arg; done
case "$last" in
*{ending}) if test "${{FIXTURE_FAILURE:-}}" = {stage}; then exit 23; fi ;;
esac
exec "{real}" "$@"''')
        self.protected = self.home / '.config/plasma-org.kde.plasma.desktop-appletsrc'
        self.protected.parent.mkdir()
        self.protected.write_text('keep my panel')
        self.database = self.home / '.local/share/ev/memory.db'
        self.database.write_bytes(b'do not touch conversation storage')
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(self.commands) + ':' + os.environ['PATH'],
                        XDG_DATA_HOME=str(self.home / '.local/share'), XDG_STATE_HOME=str(self.home / '.local/state'),
                        XDG_CONFIG_HOME=str(self.home / '.config'), CARLOS_INSTALL_TEST='1',
                        FIXTURE_EVENTS=str(self.events), FIXTURE_ACTIVE=str(self.active))

    def shell(self, path, body):
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)

    def install(self, failure='', no_start=False):
        result = subprocess.run(['sh', str(self.project / 'scripts/install-user.sh')]
                                + (['--no-start'] if no_start else []),
                                env={**self.env, 'FIXTURE_FAILURE': failure},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(self.protected.read_text(), 'keep my panel')
        self.assertEqual(self.database.read_bytes(), b'do not touch conversation storage')
        return result

    def assert_old(self):
        self.assertEqual((self.app / 'core/version').read_text(), 'old')
        self.assertTrue((self.bin / 'evctl').is_file())

    def test_install_stops_old_core_before_replacement(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.app / 'core/version').read_text(), 'new')
        actions = [line for line in self.events.read_text().splitlines() if line != 'dbus']
        self.assertEqual(actions, ['stop old', 'activate new'])
        backups = list((self.home / '.local/state/ev/install-backups').glob('*/files/app/core/version'))
        self.assertEqual([p.read_text() for p in backups], ['old'])

    def test_failed_runtime_preparation_leaves_install_running_and_untouched(self):
        if 'prepare-runtime.py' not in (PROJECT / 'scripts/install-user.sh').read_text():
            self.skipTest('This installer uses system Python')
        self.assertNotEqual(self.install('runtime').returncode, 0)
        self.assert_old()
        self.assertTrue(self.active.exists())
        self.assertFalse(self.events.exists())

    def test_failed_backup_does_not_remove_live_files(self):
        result = self.install('backup')
        self.assertNotEqual(result.returncode, 0)
        self.assert_old()
        self.assertTrue(self.active.exists())
        self.assertFalse(self.events.exists())

    def test_partial_install_rolls_back_and_restarts_old_core(self):
        result = self.install('copy')
        self.assertNotEqual(result.returncode, 0)
        self.assert_old()
        self.assertTrue(self.active.exists())
        self.assertIn('activate old', self.events.read_text())
        self.assertNotIn('Restore needs attention', result.stderr)

    def test_failed_new_health_restores_previous_install(self):
        result = self.install('health')
        self.assertNotEqual(result.returncode, 0)
        self.assert_old()
        self.assertIn('activate old', self.events.read_text())
        self.assertIn('Previous installation restored and health-checked', result.stderr)

    def test_no_start_failure_restores_without_touching_session(self):
        result = self.install('copy', no_start=True)
        self.assertNotEqual(result.returncode, 0)
        self.assert_old()
        self.assertFalse(self.events.exists())
        self.assertNotIn('Restore needs attention', result.stderr)

    def test_repeated_installs_keep_distinct_backups(self):
        self.assertEqual(self.install(no_start=True).returncode, 0)
        self.assertEqual(self.install(no_start=True).returncode, 0)
        manifests = list((self.home / '.local/state/ev/install-backups').glob('*/manifest.tsv'))
        self.assertEqual(len(manifests), 2)

    def test_running_core_that_refuses_stop_blocks_replacement(self):
        result = self.install('stuck')
        self.assertNotEqual(result.returncode, 0)
        self.assert_old()
        self.assertTrue(self.active.exists())
        self.assertIn('did not stop', result.stderr)

    def test_session_bus_error_is_not_a_successful_stop(self):
        result = self.install('bus')
        self.assertNotEqual(result.returncode, 0)
        self.assert_old()
        self.assertIn('Cannot check', result.stderr)
