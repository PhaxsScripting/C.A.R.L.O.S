import unittest
from unittest.mock import patch
from ev.events import PhaxEventBus
from ev.presence import PresenceMonitor


class PresenceTests(unittest.TestCase):
    def test_unlocked_does_not_claim_identity_or_occupancy(self):
        p = PresenceMonitor(PhaxEventBus())
        p.observe_lock(False, 100)
        p.state["idle_seconds"] = 10
        self.assertEqual(p.state["presence"], "UNKNOWN")
        self.assertEqual(p.state["person_identity"], "UNVERIFIED")

    def test_greeting_requires_away_time_and_cooldown(self):
        bus = PhaxEventBus()
        p = PresenceMonitor(bus)
        p.observe_lock(True, 2000)
        p.observe_lock(False, 2301)
        p.observe_lock(True, 2400)
        p.observe_lock(False, 2800)
        self.assertEqual(sum(e["type"] == "presence.returned" for e in bus.history()), 1)
        p.observe_lock(True, 4100)
        p.observe_lock(False, 4500)
        self.assertEqual(sum(e["type"] == "presence.returned" for e in bus.history()), 2)

    def test_explicit_address_engages_and_privacy_clears_attention(self):
        bus = PhaxEventBus()
        p = PresenceMonitor(bus)
        p.consume(bus.publish("wake.detected", "voice"))
        self.assertEqual(p.state["attention"], "ENGAGED")
        p.consume(bus.publish("carlos.privacy_changed", "privacy"))
        self.assertEqual(p.state["attention"], "DORMANT")
        self.assertEqual(p.last_addressed, 0)
        self.assertEqual(p.state["presence"], "UNKNOWN")

    def test_hand_presence_requires_fresh_confident_metadata_and_unlocked_session(self):
        p = PresenceMonitor(PhaxEventBus())
        p.observe_lock(False, 100)
        p.state["idle_seconds"] = 10
        sample = {
            "state": "READY",
            "tracking": {"hand_visible": True, "confidence": 0.95, "age_ms": 30},
        }
        p.observe_hand(sample, 100)
        self.assertEqual(p.state["presence"], "AT_DESK")
        self.assertEqual(p.state["person_identity"], "UNVERIFIED")
        sample["tracking"]["age_ms"] = 5000
        p.observe_hand(sample, 101)
        self.assertEqual(p.state["presence"], "UNKNOWN")
        sample["tracking"]["age_ms"] = 30
        p.observe_lock(True, 102)
        p.observe_hand(sample, 102)
        self.assertEqual(p.state["presence"], "LIKELY_ABSENT")

    def test_disabled_hand_presence_never_uses_camera_evidence(self):
        p = PresenceMonitor(PhaxEventBus(), {"hand_presence": False})
        p.observe_lock(False, 100)
        p.observe_hand(
            {"state": "READY", "tracking": {"hand_visible": True, "confidence": 0.99, "age_ms": 0}},
            100,
        )
        self.assertEqual(p.state["presence"], "UNKNOWN")
        self.assertFalse(p.state["camera_used"])

    def test_greeting_can_be_disabled(self):
        bus = PhaxEventBus()
        p = PresenceMonitor(bus, {"greetings": False})
        p.observe_lock(True, 2000)
        p.observe_lock(False, 2400)
        self.assertFalse(any(e["type"] == "presence.returned" for e in bus.history()))

    def test_first_return_does_not_wait_for_system_uptime(self):
        bus=PhaxEventBus(); p=PresenceMonitor(bus)
        p.observe_lock(True,10); p.observe_lock(False,311)
        self.assertEqual(sum(e['type']=='presence.returned' for e in bus.history()),1)

    def test_greeting_timing_changes_apply_without_restart(self):
        bus=PhaxEventBus(); config={'away_seconds':60,'cooldown_seconds':300}
        p=PresenceMonitor(bus,config)
        p.observe_lock(True,10); p.observe_lock(False,71)
        p.observe_lock(True,100); p.observe_lock(False,161)
        self.assertEqual(sum(e['type']=='presence.returned' for e in bus.history()),1)
        config['away_seconds']=300
        p.observe_lock(True,400); p.observe_lock(False,461)
        self.assertEqual(sum(e['type']=='presence.returned' for e in bus.history()),1)
        p.observe_lock(True,500); p.observe_lock(False,801)
        self.assertEqual(sum(e['type']=='presence.returned' for e in bus.history()),2)

    def test_lock_clears_address_and_queued_activity_cannot_restore_it(self):
        bus = PhaxEventBus()
        p = PresenceMonitor(bus)
        p.observe_lock(False, 100)
        with patch('ev.presence.time.monotonic', return_value=100):
            p.consume(bus.publish('command.received', 'test'))
        p.observe_lock(True, 101)
        self.assertEqual(p.last_addressed, 0)
        for kind in ('wake.detected', 'command.received', 'voice.transcription_complete',
                     'voice.listening_started', 'voice.barge_in', 'carlos.privacy_changed'):
            with self.subTest(kind=kind), patch('ev.presence.time.monotonic', return_value=102):
                p.consume(bus.publish(kind, 'test'))
                self.assertEqual(p.state['presence'], 'LIKELY_ABSENT')
                self.assertEqual(p.state['attention'], 'DORMANT')
                self.assertEqual(p.last_addressed, 0)
        p.observe_lock(False, 103)
        self.assertEqual(p.state['presence'], 'UNKNOWN')
        self.assertEqual(p.state['attention'], 'DORMANT')
        with patch('ev.presence.time.monotonic', return_value=104):
            p.consume(bus.publish('command.received', 'test'))
        self.assertEqual(p.state['presence'], 'ENGAGED')

    def test_malformed_hand_metadata_never_proves_presence(self):
        bad = [None, [], {'tracking': None}, {'tracking': []}]
        for key in ('confidence', 'age_ms'):
            for value in (True, False, None, '1', float('nan'), float('inf'), -1, 10**1000):
                tracking = {'hand_visible': True, 'confidence': .95, 'age_ms': 20}
                tracking[key] = value
                bad.append({'state': 'READY', 'tracking': tracking})
        bad.append({'state': 'READY', 'tracking': {'hand_visible': True, 'confidence': 1.01, 'age_ms': 0}})
        for sample in bad:
            with self.subTest(sample=sample):
                p = PresenceMonitor(PhaxEventBus())
                p.observe_lock(False, 100)
                p.state['idle_seconds'] = 10
                p.observe_hand({'state': 'READY', 'tracking': {'hand_visible': True, 'confidence': .95, 'age_ms': 20}}, 100)
                self.assertEqual(p.state['presence'], 'AT_DESK')
                p.observe_hand(sample, 101)
                self.assertEqual(p.state['presence'], 'UNKNOWN')
                self.assertFalse(p.state['camera_used'])

    def test_invalid_idle_cannot_promote_hand_presence_to_at_desk(self):
        for idle in (True, False, None, -1, float('nan'), float('inf'), '10'):
            with self.subTest(idle=idle):
                p = PresenceMonitor(PhaxEventBus())
                p.observe_lock(False, 100)
                p.state['idle_seconds'] = idle
                p.observe_hand({'state': 'READY', 'tracking': {'hand_visible': True, 'confidence': .95, 'age_ms': 20}}, 100)
                self.assertEqual(p.state['presence'], 'PRESENT')
                self.assertEqual(p.state['person_identity'], 'UNVERIFIED')

    def test_future_or_invalid_address_does_not_hold_engagement(self):
        for addressed in (True, '100', 101, float('nan'), float('inf'), 10**1000):
            with self.subTest(addressed=addressed):
                p = PresenceMonitor(PhaxEventBus())
                p.last_addressed = addressed
                p.observe_lock(False, 100)
                self.assertEqual(p.state['presence'], 'UNKNOWN')
                p.observe_hand({}, 100)
                self.assertEqual(p.state['attention'], 'DORMANT')
                self.assertEqual(p.state['presence'], 'UNKNOWN')

    def test_explicit_interaction_and_privacy_clear_old_camera_evidence(self):
        bus = PhaxEventBus()
        p = PresenceMonitor(bus)
        p.observe_lock(False, 100)
        sample = {'state': 'READY', 'tracking': {'hand_visible': True, 'confidence': .95, 'age_ms': 20}}
        for kind in ('command.received', 'carlos.privacy_changed'):
            with self.subTest(kind=kind):
                p.last_addressed = 0
                p.observe_hand(sample, 100)
                self.assertTrue(p.state['camera_used'])
                with patch('ev.presence.time.monotonic', return_value=100):
                    p.consume(bus.publish(kind, 'test'))
                self.assertFalse(p.state['camera_used'])
