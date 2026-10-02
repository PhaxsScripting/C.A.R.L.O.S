import logging
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from ev.events import PhaxEventBus
from ev.tools.base import ToolContext, ToolRegistry, validate_schema
from ev.tools.native_settings import power_profile_status, power_profile_set, register_native_settings_tools
from ev.tools.power_profiles import BACKEND, PowerProfiles, status


class PowerProfileTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('dbus-run-session'), 'Disposable D-Bus runtime unavailable')
    def test_modern_and_legacy_daemons_through_real_isolated_bus(self):
        result = subprocess.run(['dbus-run-session', '--', sys.executable,
            str(Path(__file__).with_name('fixtures') / 'power_profiles_live.py')],
            capture_output=True, text=True, env=os.environ, timeout=12)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('14 modern/legacy D-Bus checks passed; real system bus untouched', result.stdout)

    def test_kde_keeps_priority_and_no_fallback_can_hide_failed_readback(self):
        with patch('ev.tools.native_settings.checked', side_effect=[
            'balanced\npower-saver', 'balanced', '', 'balanced\npower-saver', 'balanced']), \
            patch('ev.tools.power_profiles.status', side_effect=AssertionError('Changed backend')):
            result = power_profile_set({'profile': 'power-saver'}, None)
        self.assertFalse(result['verified'])
        self.assertEqual(result['backend'], 'PowerDevil PowerProfile D-Bus')

    def test_unavailable_kde_uses_standard_backend(self):
        observed = dict(available=True, profiles=['balanced'], current='balanced', backend=BACKEND)
        with patch('ev.tools.native_settings.checked', side_effect=RuntimeError('No KDE')), \
            patch('ev.tools.power_profiles.status', return_value=observed):
            self.assertEqual(power_profile_status({}, None), observed)

    def test_expected_current_refuses_manual_change_before_dispatch(self):
        with patch('ev.tools.native_settings.checked', side_effect=['balanced\npower-saver', 'power-saver']) as native:
            with self.assertRaisesRegex(ValueError, 'Current profile changed'):
                power_profile_set({'profile': 'balanced', 'expected_current': 'balanced'}, None)
        self.assertEqual(native.call_count, 2)

    def test_bus_error_and_missing_dependency_are_unavailable_without_service_activation(self):
        with patch.object(PowerProfiles, '__enter__', side_effect=ImportError('No runtime')):
            observed = status()
        self.assertFalse(observed['available'])
        self.assertIsNone(observed['current'])
        self.assertEqual(observed['profiles'], [])

    def test_profile_contracts_cover_unavailable_and_successful_results(self):
        registry = ToolRegistry(ToolContext({}, PhaxEventBus(), logging.getLogger('power-profile-contract')))
        register_native_settings_tools(registry)
        get = registry.get('settings.power_profile.get')
        set_profile = registry.get('settings.power_profile.set')
        self.assertTrue(get.read_only)
        for spec in (get, set_profile):
            self.assertFalse(spec.public()['contract_gaps'])
            validate_schema(dict(available=False, current=None, profiles=[], backend=BACKEND, reason='Absent'), spec.output_schema)
        validate_schema(dict(available=True, current='balanced', profiles=['balanced'], backend=BACKEND,
                             reason='Observed', verified=True, already_set=True), set_profile.output_schema)
        with self.assertRaises(ValueError):
            validate_schema({'profile': '--help'}, set_profile.schema)


    def test_malformed_daemon_metadata_never_becomes_available(self):
        from types import SimpleNamespace
        original = {'Profiles': ('aa{sv}', [{'Profile': ('s', 'balanced')}]), 'ActiveProfile': ('s', 'balanced')}
        for replacements in (
            {'Profiles': ('s', 'balanced')}, {'Profiles': ('aa{sv}', [])},
            {'Profiles': ('aa{sv}', [{'Profile': ('s', 'balanced')}] * 2)},
            {'Profiles': ('aa{sv}', [{'Profile': ('s', '--help')}])},
            {'ActiveProfile': ('s', 'performance')}, {'ActiveProfile': ('b', True)},
            {'PerformanceDegraded': ('b', False)},
        ):
            daemon = PowerProfiles()
            daemon.properties = SimpleNamespace(get_all=lambda: 'fixture-read')
            with patch.object(daemon, '_check_owner'), patch.object(daemon, '_call', return_value=(dict(original, **replacements),)):
                with self.assertRaises(ValueError):
                    daemon.read()


    def test_empty_native_choices_report_no_current_profile(self):
        with patch('ev.tools.native_settings.checked', return_value=''), patch('ev.tools.power_profiles.status', return_value=dict(available=False, profiles=[], current=None)):
            result = power_profile_status({}, None)
        self.assertFalse(result['available'])
        self.assertIsNone(result['current'])
        self.assertEqual(result['profiles'], [])
