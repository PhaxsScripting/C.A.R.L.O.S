import unittest
from ev.events import PhaxEventBus


class EventMetricTests(unittest.TestCase):
    def test_empty_metrics_do_not_invent_a_success_rate(self):
        metrics = PhaxEventBus().metrics()
        self.assertEqual(metrics['queued_deliveries'], 0)
        self.assertEqual(metrics['queue_capacity'], 0)
        self.assertIsNone(metrics['tools']['success_percent'])

    def test_overflow_counts_deliveries_and_keeps_queue_join_balanced(self):
        bus = PhaxEventBus(queue_size=2)
        first, q1 = bus.subscribe()
        _, q2 = bus.subscribe()
        for n in range(5):
            bus.publish('task.updated', 'executor', {'n': n})
        metrics = bus.metrics()
        self.assertEqual(metrics['published_events'], 5)
        self.assertEqual(metrics['subscribers'], 2)
        self.assertEqual(metrics['queued_deliveries'], 4)
        self.assertEqual(metrics['queue_capacity'], 4)
        self.assertEqual(metrics['max_queue_depth'], 2)
        self.assertEqual(metrics['dropped_deliveries'], 6)
        for queue in (q1, q2):
            self.assertEqual([queue.get_nowait().payload['n'] for _ in range(2)], [3, 4])
            queue.task_done()
            queue.task_done()
            self.assertEqual(queue._unfinished_tasks, 0)
        bus.unsubscribe(first)
        self.assertEqual(bus.metrics()['queue_capacity'], 2)

    def test_failed_completed_results_count_as_failures_and_verification_is_separate(self):
        bus = PhaxEventBus()
        bus.publish('tool.completed', 'tools', {'tool': 'fixture', 'ok': True})
        bus.publish('tool.completed', 'tools', {'tool': 'fixture', 'ok': True,
                                              'execution': {'verified': True}})
        bus.publish('tool.completed', 'tools', {'tool': 'fixture', 'ok': False})
        bus.publish('tool.failed', 'tools', {'tool': 'fixture'})
        bus.publish('tool.completed', 'tools', {'tool': 'fixture'})
        tools = bus.metrics()['tools']
        self.assertEqual(tools['succeeded'], 2)
        self.assertEqual(tools['failed'], 2)
        self.assertEqual(tools['verified'], 1)
        self.assertEqual(tools['unclassified'], 1)
        self.assertEqual(tools['success_percent'], 50)

    def test_private_composite_and_untrusted_source_results_are_excluded(self):
        bus = PhaxEventBus()
        bus.publish('tool.completed', 'tools', {'tool': 'agent.execute_plan', 'ok': True})
        bus.publish('tool.completed', 'model', {'tool': 'fixture', 'ok': True})
        bus.private = True
        bus.publish('tool.completed', 'tools', {'tool': 'fixture', 'ok': True})
        self.assertIsNone(bus.metrics()['tools']['success_percent'])
        self.assertNotIn('fixture', str(bus.metrics()))

    def test_malformed_verification_metadata_does_not_break_event_delivery(self):
        bus = PhaxEventBus()
        _, queue = bus.subscribe()
        bus.publish('tool.completed', 'tools', {'tool': 'fixture', 'ok': True, 'execution': None})
        self.assertEqual(queue.qsize(), 1)
        self.assertEqual(bus.metrics()['tools']['verified'], 0)
