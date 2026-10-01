import asyncio
import threading
import unittest

from ev.events import PhaxEventBus


class ThreadedEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_wakes_waiting_consumer_on_its_own_loop(self):
        bus = PhaxEventBus()
        _, queue = bus.subscribe()
        consumer = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        event = await asyncio.to_thread(bus.publish, 'tool.completed', 'fixture', {'ok': True})
        received = await asyncio.wait_for(consumer, 1)
        self.assertIs(received, event)
        queue.task_done()
        await asyncio.wait_for(queue.join(), 1)

    async def test_owner_cannot_overtake_pending_thread_publications(self):
        bus = PhaxEventBus()
        _, queue = bus.subscribe()
        worker = threading.Thread(target=lambda: bus.publish('task.started', 'fixture'))
        worker.start()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        bus.publish('task.updated', 'fixture')
        first = await asyncio.wait_for(queue.get(), 1)
        second = await asyncio.wait_for(queue.get(), 1)
        self.assertEqual([first.type, second.type], ['task.started', 'task.updated'])
        self.assertLess(first.sequence, second.sequence)
        queue.task_done(); queue.task_done()

    async def test_worker_overflow_keeps_priority_and_balanced_join(self):
        bus = PhaxEventBus(queue_size=2)
        _, queue = bus.subscribe()
        consumer = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        def publish():
            for index in range(30):
                bus.publish('system.telemetry', 'fixture', {'sample': index})
            bus.publish('voice.barge_in', 'voice')
        await asyncio.to_thread(publish)
        received = [await asyncio.wait_for(consumer, 1)]
        while not queue.empty():
            received.append(queue.get_nowait())
        for _ in received:
            queue.task_done()
        await asyncio.wait_for(queue.join(), 1)
        self.assertTrue(any(event.type == 'voice.barge_in' for event in received))
        self.assertGreater(bus.metrics()['dropped_deliveries'], 0)

    async def test_cancelled_consumer_does_not_lose_next_worker_event(self):
        bus = PhaxEventBus()
        _, queue = bus.subscribe()
        consumer = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        await asyncio.to_thread(bus.publish, 'core.started', 'fixture')
        received = await asyncio.wait_for(queue.get(), 1)
        self.assertEqual(received.type, 'core.started')
        queue.task_done()

    async def test_thread_ingress_is_bounded_before_loop_can_drain_it(self):
        bus = PhaxEventBus(queue_size=4)
        _, queue = bus.subscribe()
        def publish():
            for _ in range(1000):
                bus.publish('system.telemetry', 'fixture')
            bus.publish('voice.barge_in', 'voice')
        worker = threading.Thread(target=publish)
        worker.start(); worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertLessEqual(len(bus._pending_dispatch), 4)
        await asyncio.sleep(0)
        rows = []
        while not queue.empty():
            rows.append(queue.get_nowait()); queue.task_done()
        self.assertTrue(any(event.type == 'voice.barge_in' for event in rows))
        await asyncio.wait_for(queue.join(), 1)

    async def test_new_subscriber_does_not_receive_older_pending_events(self):
        bus = PhaxEventBus()
        _, old = bus.subscribe()
        worker = threading.Thread(target=lambda: bus.publish('core.started', 'fixture'))
        worker.start(); worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        _, new = bus.subscribe()
        bus.publish('core.stopping', 'fixture')
        self.assertEqual(old.qsize(), 2)
        self.assertEqual(new.qsize(), 1)
        self.assertEqual(new.get_nowait().type, 'core.stopping')

    async def test_private_switch_clears_history_and_tags_later_worker_events(self):
        bus = PhaxEventBus()
        bus.publish('core.started', 'fixture')
        bus.set_private(True)
        event = await asyncio.to_thread(bus.publish, 'tool.completed', 'fixture', {'ok': True})
        self.assertTrue(event.private)
        self.assertEqual(len(bus.history()), 1)
        self.assertEqual(bus.metrics()['tools']['succeeded'], 0)

    async def test_unbounded_and_invalid_ingress_sizes_are_rejected(self):
        for value in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                PhaxEventBus(queue_size=value)
