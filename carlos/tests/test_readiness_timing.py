import time
import unittest
from ev.events import PhaxEventBus
from ev.presence import PresenceMonitor
from ev.readiness import ReadinessTiming, project_readiness


class ReadinessTimingTests(unittest.TestCase):
    def ready(self):
        return {'state': 'FULLY_READY', 'components': {'Core': 'READY', 'Voice': 'READY'}}

    def resumed(self, timer, stamp=200, interval=7.5):
        event = PhaxEventBus().publish('system.resume_observed', 'presence', {
            'observed_monotonic': stamp, 'detection_interval_seconds': interval})
        timer.resumed(event)
        return event

    def test_records_first_observation_without_rewriting_it_on_later_polls(self):
        timer = ReadinessTiming(100)
        timer.observe({'state':'PARTIAL','components':{'Core':'READY','Voice':'STARTING'}}, 101)
        timer.observe(self.ready(), 103)
        timer.observe(self.ready(), 109)
        result = timer.snapshot()
        self.assertEqual(result['startup_components_seconds'], {'Core':1,'Voice':3})
        self.assertEqual(result['startup_to_all_ready_seconds'], 3)
        self.assertIsNone(result['last_resume'])
        result['startup_components_seconds']['Core'] = 99
        self.assertEqual(timer.snapshot()['startup_components_seconds']['Core'], 1)

    def test_resume_includes_detection_uncertainty_and_keeps_startup_sample(self):
        timer = ReadinessTiming(100)
        timer.observe(self.ready(), 103)
        self.resumed(timer)
        timer.observe(self.ready(), 202)
        result = timer.snapshot()
        self.assertEqual(result['startup_to_all_ready_seconds'], 3)
        self.assertEqual(result['last_resume']['detection_to_all_ready_seconds'], 2)
        self.assertEqual(result['last_resume']['resume_to_all_ready_range_seconds'], [2,9.5])
        self.assertNotIn('monotonic', result['last_resume'])

    def test_next_resume_resets_only_resume_measurements(self):
        timer = ReadinessTiming(100)
        self.resumed(timer)
        timer.observe(self.ready(), 202)
        self.resumed(timer, stamp=300)
        self.assertIsNone(timer.snapshot()['last_resume']['detection_to_all_ready_seconds'])
        self.assertEqual(timer.snapshot()['startup_to_all_ready_seconds'], 102)
        self.resumed(timer, stamp=200)
        self.assertEqual(timer.resume['monotonic'], 300)

    def test_invalid_resume_or_future_observation_cannot_record_readiness(self):
        timer = ReadinessTiming(100)
        for value in [None, True, float('nan'), float('inf'), -1]:
            self.resumed(timer, stamp=value)
            self.assertIsNone(timer.resume)
        self.resumed(timer)
        timer.observe(self.ready(), 190)
        self.assertIsNone(timer.snapshot()['last_resume']['detection_to_all_ready_seconds'])

    def test_pre_suspend_health_does_not_count_as_fresh_recovery(self):
        now = time.time()
        s = {'voice':{}, 'health':{'Local AI':{'state':'READY','observed_at':now-1}}}
        rows = {'Remote':{'state':'READY','checked_at':now-1}}
        r = project_readiness(s, rows, fresh_after=now)
        self.assertEqual(r['components']['Local AI'], 'STALE')
        self.assertEqual(r['components']['Remote'], 'STALE')

    def test_clock_observation_detects_sleep_once_with_actual_sampling_gap(self):
        bus = PhaxEventBus()
        presence = PresenceMonitor(bus)
        presence.observe_clock(100, 100)
        presence.observe_clock(105, 105)
        self.assertEqual(bus.history(), [])
        presence.observe_clock(412, 112)
        events = bus.history()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['payload']['suspended_seconds'], 300)
        self.assertEqual(events[0]['payload']['detection_interval_seconds'], 7)
        presence.observe_clock(417, 117)
        self.assertEqual(len(bus.history()), 1)
