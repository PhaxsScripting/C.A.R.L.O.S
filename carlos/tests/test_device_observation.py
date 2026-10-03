import subprocess
import unittest
from unittest.mock import patch

from ev.tools.base import validate_schema
from ev.tools.builtin import get_connected_devices
from ev.tools.results import evaluate_result
from ev.tools.system_contracts import SYSTEM_OBSERVATION_SCHEMAS


class DeviceObservationTests(unittest.TestCase):
    audio = {'backend': 'fixture', 'default_input': '', 'default_output': '', 'inputs': [], 'outputs': []}

    def observe(self, commands, audio=None):
        with patch('ev.tools.builtin.run_command', side_effect=commands), patch(
                'ev.tools.builtin.get_audio_devices', return_value=self.audio, side_effect=audio):
            result = get_connected_devices({}, None)
        validate_schema(result, SYSTEM_OBSERVATION_SCHEMAS['system.devices'])
        return result

    def test_failed_bluetooth_inventory_is_unknown_and_keeps_other_readings(self):
        result = self.observe([
            {'ok': True, 'stdout': 'Bus 001 Device 001: Fixture\n', 'stderr': ''},
            {'ok': False, 'stdout': 'Device old partial output\n', 'stderr': 'No default controller'},
        ])
        self.assertTrue(result['ok'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['usb'], ['Bus 001 Device 001: Fixture'])
        self.assertIsNone(result['bluetooth'])
        self.assertFalse(result['inventories']['bluetooth']['available'])
        self.assertEqual(result['audio'], self.audio)
        self.assertFalse(evaluate_result('system.devices', result).verified)

    def test_successful_empty_inventory_is_distinct_from_unavailable(self):
        result = self.observe([{'ok': True, 'stdout': '', 'stderr': ''}] * 2)
        self.assertTrue(result['complete'])
        self.assertEqual(result['usb'], [])
        self.assertEqual(result['bluetooth'], [])
        self.assertTrue(all(row['available'] for row in result['inventories'].values()))

    def test_missing_timed_out_and_failed_audio_backends_cannot_report_empty_success(self):
        result = self.observe([FileNotFoundError('missing fixture tool'), subprocess.TimeoutExpired('fixture', 5)],
                              RuntimeError('audio backend unavailable'))
        self.assertFalse(result['ok'])
        self.assertFalse(result['complete'])
        self.assertIsNone(result['usb'])
        self.assertIsNone(result['bluetooth'])
        self.assertIsNone(result['audio'])
        self.assertTrue(all(row['detail'] and not row['available'] for row in result['inventories'].values()))
        self.assertFalse(evaluate_result('system.devices', result).ok)

    def test_truncated_inventory_does_not_claim_complete_device_list(self):
        result = self.observe([
            {'ok': True, 'stdout': 'incomplete USB output', 'stderr': '', 'truncated': True},
            {'ok': True, 'stdout': 'Device AA:BB Fixture\n', 'stderr': ''},
        ])
        self.assertFalse(result['complete'])
        self.assertIsNone(result['usb'])
        self.assertFalse(result['inventories']['usb']['available'])
        self.assertEqual(result['bluetooth'], ['AA:BB Fixture'])
