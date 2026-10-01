from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import unittest
from ev.battery import read_battery


class BatteryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.summary = SimpleNamespace(percent=0, power_plugged=True, secsleft=-2)
        mock = patch('ev.battery.psutil.sensors_battery', return_value=self.summary)
        self.battery = mock.start()
        self.addCleanup(mock.stop)

    def supply(self, name='BAT0', **values):
        path = self.root / name
        path.mkdir(exist_ok=True)
        for key, value in {'type':'Battery', **values}.items():
            (path/key).write_text(str(value))
        return path

    def test_plugged_in_does_not_mean_charging(self):
        self.supply(status='Not charging', current_now=1000, voltage_now=13570000)
        result = read_battery(self.root)
        self.assertTrue(result['plugged'])
        self.assertFalse(result['charging'])
        self.assertEqual(result['status'], 'Not charging')
        self.assertAlmostEqual(result['cells'][0]['power_watts'], .0136)

    def test_direct_power_and_temperature_units(self):
        self.supply(status='Charging', power_now=24000000, temp=315)
        result = read_battery(self.root)
        self.assertTrue(result['charging'])
        self.assertEqual(result['cells'][0]['power_watts'], 24)
        self.assertEqual(result['cells'][0]['temperature_celsius'], 31.5)

    def test_discharge_does_not_rely_on_current_sign(self):
        self.supply(status='Discharging', current_now=2000000, voltage_now=12000000)
        self.assertFalse(read_battery(self.root)['charging'])

    def test_missing_or_invalid_sensor_stays_unknown(self):
        self.supply(status='unrecognized', power_now='nan', current_now='bad', temp='inf')
        result = read_battery(self.root)
        self.assertIsNone(result['charging'])
        self.assertIsNone(result['cells'][0]['power_watts'])
        self.assertIsNone(result['cells'][0]['temperature_celsius'])

    def test_external_device_and_removed_battery_are_excluded(self):
        self.supply('mouse', scope='Device', status='Charging')
        self.supply('BAT1', present=0, status='Charging')
        self.assertEqual(read_battery(self.root)['cells'], [])

    def test_different_battery_states_are_not_collapsed_to_charging(self):
        self.supply(status='Charging')
        self.supply('BAT1', status='Discharging')
        result = read_battery(self.root)
        self.assertEqual(result['status'], 'Mixed')
        self.assertIsNone(result['charging'])

    def test_absent_battery_is_not_zero_percent(self):
        self.battery.return_value = None
        self.assertIsNone(read_battery(self.root))

    def test_platform_without_sysfs_keeps_summary_without_guessing(self):
        result = read_battery(self.root / 'missing')
        self.assertEqual(result['percent'], 0)
        self.assertIsNone(result['charging'])
        self.assertEqual(result['status_source'], 'unavailable')
