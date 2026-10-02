import asyncio
import json
import logging
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from ev.events import PhaxEventBus
from ev.hardware_metrics import HardwareMetrics, read_int
from ev.telemetry import TelemetrySampler, read_temperature
from ev.tools.base import ToolContext, ToolRegistry
from ev.tools.hardware import register_hardware_tools
from ev.tools.results import evaluate_result


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 10.0
        self.sampler = HardwareMetrics(self.root, clock=lambda: self.now)

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value))
        return path

    def counter(self, value, cpu='cpu0', kind='core'):
        return self.write(f'devices/system/cpu/{cpu}/thermal_throttle/{kind}_throttle_count', value)

    def test_missing_sysfs_is_unknown_not_zero(self):
        result = self.sampler.sample()
        for key in ('cpu_frequency', 'cpu_throttling', 'fans', 'gpu_usage'):
            self.assertEqual(result[key]['status'], 'UNAVAILABLE')
        self.assertIsNone(result['cpu_throttling']['events_observed'])
        self.assertIsNone(result['cpu_throttling']['currently_throttling'])
        json.dumps(result, allow_nan=False)

    def test_shared_clock_policy_is_read_once_with_units_and_source(self):
        prefix = 'devices/system/cpu/cpufreq/policy0/'
        self.write(prefix + 'related_cpus', '0 1')
        self.write(prefix + 'cpuinfo_cur_freq', '2400123')
        self.write(prefix + 'scaling_cur_freq', '2800000')
        link = self.root / 'devices/system/cpu/cpu1/cpufreq'
        link.parent.mkdir(parents=True)
        link.symlink_to(self.root / 'devices/system/cpu/cpufreq/policy0')
        result = self.sampler.sample()['cpu_frequency']
        self.assertEqual(len(result['policies']), 1)
        self.assertEqual(result['policies'][0]['cpus'], [0, 1])
        self.assertEqual(result['policies'][0]['current_mhz'], 2400.123)
        self.assertEqual(result['policies'][0]['source'], 'cpuinfo_cur_freq')

    def test_average_and_scaling_fallback_are_labelled_and_zero_is_unknown(self):
        prefix = 'devices/system/cpu/cpufreq/policy0/'
        self.write(prefix + 'cpuinfo_cur_freq', '0')
        average = self.write(prefix + 'cpuinfo_avg_freq', '1500000')
        self.write(prefix + 'scaling_cur_freq', '1900000')
        self.assertEqual(self.sampler.sample()['cpu_frequency']['policies'][0]['source'], 'cpuinfo_avg_freq')
        average.write_text('nan')
        self.assertEqual(self.sampler.sample()['cpu_frequency']['policies'][0]['source'], 'scaling_cur_freq')
        self.write('devices/system/cpu/cpufreq/policy1/scaling_cur_freq', '-1')
        self.assertEqual(self.sampler.sample()['cpu_frequency']['status'], 'PARTIAL')

    def test_old_throttle_counts_do_not_mean_new_or_current_throttling(self):
        self.counter(99)
        initial = self.sampler.sample()['cpu_throttling']
        self.assertIsNone(initial['events_observed'])
        self.now += 3
        second = self.sampler.sample()['cpu_throttling']
        self.assertFalse(second['events_observed'])
        self.assertEqual(second['counters'][0]['delta'], 0)
        self.assertEqual(second['counters'][0]['interval_seconds'], 3)
        self.assertIsNone(second['currently_throttling'])

    def test_counter_delta_does_not_sum_duplicate_package_events(self):
        self.counter(5, kind='package')
        self.counter(5, cpu='cpu1', kind='package')
        self.sampler.sample()
        self.now += 2
        self.counter(6, kind='package')
        self.counter(6, cpu='cpu1', kind='package')
        result = self.sampler.sample()['cpu_throttling']
        self.assertTrue(result['events_observed'])
        self.assertEqual([c['delta'] for c in result['counters']], [1, 1])
        self.assertNotIn('total_events', result)

    def test_overflow_replacement_and_backward_clock_rebaseline(self):
        path = self.counter((1 << 64) - 1)
        self.sampler.sample()
        self.now += 1
        path.write_text('0')
        result = self.sampler.sample()['cpu_throttling']
        self.assertTrue(result['counters'][0]['baseline_reset'])
        self.assertIsNone(result['events_observed'])
        old = path.with_suffix('.old')
        path.rename(old)
        path.write_text('100')
        self.now += 1
        self.assertIsNone(self.sampler.sample()['cpu_throttling']['events_observed'])
        self.now -= 2
        self.assertIsNone(self.sampler.sample()['cpu_throttling']['events_observed'])

    def test_disappearing_counter_never_reuses_stale_baseline(self):
        path = self.counter(5)
        self.sampler.sample()
        path.unlink()
        self.now += 1
        self.assertEqual(self.sampler.sample()['cpu_throttling']['status'], 'UNAVAILABLE')
        path.write_text('50')
        self.now += 1
        self.assertIsNone(self.sampler.sample()['cpu_throttling']['events_observed'])

    def test_fan_zero_fault_disabled_and_invalid_are_distinct(self):
        prefix = 'class/hwmon/hwmon0/'
        self.write(prefix + 'name', 'dell_smm')
        self.write(prefix + 'fan1_input', 0)
        self.write(prefix + 'fan2_input', 3000)
        self.write(prefix + 'fan2_fault', 1)
        self.write(prefix + 'fan3_input', 2000)
        self.write(prefix + 'fan3_enable', 0)
        self.write(prefix + 'fan4_input', '-22')
        result = self.sampler.sample()['fans']
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual([r['rpm'] for r in result['readings']], [0, None, None, None])
        self.assertEqual(result['readings'][1]['fault'], 1)

    def test_gpu_cards_exclude_connectors_and_render_nodes(self):
        self.write('class/drm/card0/device/gpu_busy_percent', 0)
        self.write('class/drm/card0/device/mem_busy_percent', 65)
        self.write('class/drm/card1/device/gpu_busy_percent', 101)
        self.write('class/drm/card0-HDMI-A-1/device/gpu_busy_percent', 50)
        self.write('class/drm/renderD128/device/gpu_busy_percent', 80)
        result = self.sampler.sample()['gpu_usage']
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual([d['card'] for d in result['devices']], ['card0', 'card1'])
        self.assertEqual([d['busy_percent'] for d in result['devices']], [0, None])

    def test_unreadable_and_malformed_values_are_isolated(self):
        bad = self.write('class/hwmon/hwmon0/fan1_input', 1000)
        self.write('class/hwmon/hwmon1/fan1_input', 2000)
        original = Path.open
        def denied(path, *args, **kwargs):
            if path == bad:
                raise PermissionError('fixture')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', denied):
            self.assertEqual(self.sampler.sample()['fans']['status'], 'PARTIAL')
        for value in ('nan', 'inf', '-1', '1.5', 'true', '9' * 5000):
            bad.write_text(value)
            self.assertIsNone(read_int(bad, 100))

    def test_sampler_never_writes_sensor_files(self):
        self.counter(12)
        self.write('class/hwmon/hwmon0/fan1_input', 2500)
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.sampler.sample()
        self.now += 1
        self.sampler.sample()
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_invalid_temperature_cannot_bypass_model_thermal_guard(self):
        self.write('class/hwmon/hwmon0/name', 'coretemp')
        self.write('class/hwmon/hwmon0/temp1_label', 'Package id 0')
        path = self.write('class/hwmon/hwmon0/temp1_input', 'nan')
        for value in ('nan', 'inf', '-inf', '-300000', 'unavailable'):
            path.write_text(value)
            result = read_temperature(self.root / 'class/hwmon')
            self.assertIsNone(result['celsius'])
            json.dumps(result, allow_nan=False)

    def test_faulted_package_and_disabled_sensors_are_not_trusted(self):
        prefix = 'class/hwmon/hwmon0/'
        self.write(prefix + 'name', 'coretemp')
        self.write(prefix + 'temp1_label', 'Package id 0')
        self.write(prefix + 'temp1_input', 90000)
        fault = self.write(prefix + 'temp1_fault', 1)
        self.write(prefix + 'temp2_input', 45000)
        self.write(prefix + 'temp2_enable', 0)
        self.assertIsNone(read_temperature(self.root / 'class/hwmon')['celsius'])
        fault.write_text('0')
        self.assertEqual(read_temperature(self.root / 'class/hwmon')['celsius'], 90)

    def test_telemetry_uses_shared_sampler_and_keeps_existing_sensors(self):
        self.counter(3)
        telemetry = TelemetrySampler(PhaxEventBus(), hardware=self.sampler)
        self.assertIsNone(telemetry.sample()['hardware']['cpu_throttling']['events_observed'])
        self.now += 3
        self.counter(4)
        result = telemetry.sample()
        self.assertTrue(result['hardware']['cpu_throttling']['events_observed'])
        self.assertIn('cpu_temperature', result)
        self.assertIn('battery', result)


class HardwareToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_requests_use_sensor_tool_and_keep_unknowns_explicit(self):
        from ev.ai.offline import OfflineProvider
        provider = OfflineProvider()
        with tempfile.TemporaryDirectory() as directory:
            result = HardwareMetrics(Path(directory)).sample()
        for request in ('What is my fan speed?', 'Check CPU clocks', 'Show GPU usage',
                        'Is my CPU throttling?', 'Show hardware metrics'):
            turn = await provider.begin(request, [], [], [])
            self.assertEqual([c.name for c in turn.tool_calls], ['system.get_hardware_metrics'], request)
            answer = await provider.continue_with_tools(turn, [(turn.tool_calls[0], {'result': result})], [])
            self.assertIn('GPU load readings are unavailable', answer.text)
            self.assertIn('no valid comparison', answer.text)
            self.assertNotIn('0%', answer.text)

    async def test_core_catalog_has_tool_before_provider_context_is_built(self):
        from ev.paths import Paths
        from ev.service import CarlosCore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            core = CarlosCore(paths=Paths(*(root / n for n in ('config', 'data', 'state', 'cache', 'runtime'))))
            try:
                spec = core.tools.get('system.get_hardware_metrics')
                self.assertTrue(spec.read_only)
                self.assertIs(core.telemetry.hardware, core.hardware_metrics)
                self.assertIn(spec.name, [t['name'] for t in core.tools.catalog()])
            finally:
                core.memory.close()

    async def test_registered_tool_is_read_only_offline_and_runs_off_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = ToolRegistry(ToolContext({'security': {'max_tool_output_bytes': 65536}},
                                               PhaxEventBus(), logging.getLogger('hardware-test')))
            sampler = HardwareMetrics(Path(directory))
            register_hardware_tools(registry, sampler)
            spec = registry.get('system.get_hardware_metrics')
            self.assertTrue(spec.read_only)
            self.assertTrue(spec.offline_available)
            result = await registry.execute(spec, {})
            execution = evaluate_result(spec.name, result, read_only=spec.read_only)
            self.assertFalse(execution.changed_state)
            self.assertEqual(result['fans']['status'], 'UNAVAILABLE')
            loop_thread = threading.get_ident()
            with patch.object(sampler, 'sample', side_effect=lambda: {'thread': threading.get_ident()}):
                result = await spec.executor({}, registry.context)
                self.assertNotEqual(result['thread'], loop_thread)
