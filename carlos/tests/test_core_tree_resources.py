import asyncio
import copy
import logging
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import psutil
from ev.core_resources import measure_tree, read_tree, register_core_resources
from ev.events import PhaxEventBus
from ev.tools import ToolRegistry, ToolContext
from ev.commands import direct_action
from ev.tools.results import evaluate_result


def tree(rows, complete=True):
    return {'rows': rows, 'complete': complete, 'limit_reached': not complete,
            'unavailable': 0, 'discovered': len(rows)}


def row(cpu, rss=100, pss=60):
    return {'cpu_seconds': cpu, 'rss_bytes': rss, 'pss_bytes': pss}


class CoreResourceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = SimpleNamespace(pid=1, create_time=lambda: 100)

    async def measure(self, first, last):
        reader = Mock(side_effect=[copy.deepcopy(first), copy.deepcopy(last)])
        with patch('ev.core_resources.asyncio.sleep', new_callable=AsyncMock):
            return await measure_tree(self.root, 2, reader=reader, clock=Mock(side_effect=[10, 12]))

    async def test_core_and_child_are_included_without_double_counting_cpu_or_confusing_pss(self):
        result = await self.measure(tree({(1, 100): row(1), (2, 200): row(2, 1000, 900)}),
                                    tree({(1, 100): row(1.1), (2, 200): row(2.3, 1000, 900)}))
        self.assertEqual(result['process_count'], 2)
        self.assertEqual(result['rss_total_bytes'], 1100)
        self.assertEqual(result['pss_total_bytes'], 960)
        self.assertEqual(result['cpu_percent_one_core'], 20)
        self.assertTrue(result['cpu_complete'])
        self.assertIn('excludes separate UI', result['scope'])
        execution = evaluate_result('system.carlos_resources', result, read_only=True)
        self.assertFalse(execution.changed_state)
        self.assertEqual(execution.scope, 'observation_only')

    async def test_pid_reuse_birth_exit_and_counter_reset_cannot_report_complete_cpu(self):
        first = tree({(1, 100): row(1), (2, 200): row(2)})
        for last in (tree({(1, 100): row(1), (2, 300): row(2)}),
                     tree({(1, 100): row(1), (2, 200): row(2), (3, 300): row(0)}),
                     tree({(1, 100): row(1)}), tree({(1, 100): row(1), (2, 200): row(0)})):
            with self.subTest(last=last):
                result = await self.measure(first, last)
                self.assertIsNone(result['cpu_percent_one_core'])
                self.assertFalse(result['cpu_complete'])

    async def test_partial_memory_is_an_observed_lower_bound_not_a_total(self):
        first = tree({(1, 100): row(1)})
        result = await self.measure(first, tree({(1, 100): row(1)}, complete=False))
        self.assertIsNone(result['rss_total_bytes'])
        self.assertIsNone(result['pss_total_bytes'])
        self.assertEqual(result['rss_observed_bytes'], 100)
        self.assertFalse(result['memory_complete'])

    async def test_missing_pss_does_not_invalidate_rss_or_stable_cpu(self):
        result = await self.measure(tree({(1, 100): row(1, pss=None)}), tree({(1, 100): row(1.2, pss=None)}))
        self.assertEqual(result['rss_total_bytes'], 100)
        self.assertIsNone(result['pss_total_bytes'])
        self.assertFalse(result['pss_complete'])
        self.assertEqual(result['cpu_percent_one_core'], 10)

    async def test_missing_core_identity_is_a_failure_not_zero_memory(self):
        result = await self.measure(tree({(1, 100): row(1)}), tree({(1, 101): row(1)}))
        self.assertFalse(result['ok'])
        self.assertNotIn('rss_total_bytes', result)

    async def test_cancellation_does_not_take_an_after_snapshot_or_control_processes(self):
        entered = asyncio.Event()
        reader = Mock(return_value=tree({(1, 100): row(1)}))

        async def wait(seconds):
            entered.set()
            await asyncio.Future()

        with patch('ev.core_resources.asyncio.sleep', side_effect=wait):
            task = asyncio.create_task(measure_tree(self.root, 3, reader=reader))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        reader.assert_called_once()

    def test_registered_measurement_is_read_only_offline_and_interval_bounded(self):
        registry = ToolRegistry(ToolContext({'security': {'max_tool_output_bytes': 65536}}, PhaxEventBus(), logging.getLogger('test')))
        register_core_resources(registry)
        spec = registry.get('system.carlos_resources')
        self.assertTrue(spec.read_only)
        self.assertTrue(spec.offline_available)
        self.assertEqual(spec.schema['properties']['seconds']['maximum'], 15)
        self.assertEqual(spec.timeout_seconds, 20)

    def test_explicit_resource_question_routes_without_a_model_or_cleanup_action(self):
        for text in ("check Carlos resources", "show Carlos's resource usage", "how much RAM is Carlos using?"):
            with self.subTest(text=text):
                action = direct_action(text)
                self.assertIsNotNone(action)
                self.assertEqual(action.tool, 'system.carlos_resources')
                self.assertEqual(action.arguments, {})
        self.assertIsNone(direct_action('do not check Carlos resources'))

    def test_tree_reader_caps_detailed_reads_and_rejects_reused_identity(self):
        processes = []
        for pid in range(1, 67):
            process = Mock(pid=pid)
            process.create_time.return_value = pid * 10
            process.cpu_times.return_value = SimpleNamespace(user=1, system=2)
            process.memory_info.return_value = SimpleNamespace(rss=100)
            process.memory_full_info.return_value = SimpleNamespace(pss=60)
            process.oneshot.return_value = nullcontext()
            processes.append(process)
        root = processes[0]
        root.children.return_value = processes[1:]
        with patch('ev.core_resources.psutil.Process', side_effect=lambda pid: processes[pid - 1]):
            result = read_tree(root)
        self.assertEqual(len(result['rows']), 64)
        self.assertTrue(result['limit_reached'])
        self.assertFalse(result['complete'])
        processes[-1].memory_full_info.assert_not_called()
        root.children.return_value = [processes[1]]
        replacement = Mock()
        replacement.create_time.return_value = 999
        with patch('ev.core_resources.psutil.Process', side_effect=lambda pid: root if pid == 1 else replacement):
            result = read_tree(root)
        self.assertEqual(set(result['rows']), {(1, 10)})
        self.assertEqual(result['unavailable'], 1)
        self.assertFalse(result['complete'])
