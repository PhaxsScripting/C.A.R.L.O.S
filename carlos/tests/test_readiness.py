import time
import unittest
from ev.readiness import project_readiness


class ReadinessTests(unittest.TestCase):
    def ready(self):
        snapshot = {"voice": {"wake_enabled": True, "wake_active": True, "stt_available": True, "tts_available": True},
                    "desktop": {"available": True}}
        snapshot['voice']['workers'] = {name: {'mode': 'PERSISTENT', 'running': True}
                                        for name in ('VAD', 'STT', 'TTS')}
        snapshot['health'] = {name: {'state': 'READY', 'observed_at': time.time()}
                              for name in ('VAD', 'STT', 'TTS')}
        rows = {"LocalAI": {"state": "READY", "checked_at": time.time()},
                "Remote": {"state": "READY", "checked_at": time.time()}}
        return snapshot, rows

    def test_installation_and_disabled_wake_never_claim_fully_ready(self):
        result = project_readiness({"voice": {"stt_available": True, "tts_available": True}},
                                   {"LocalAI": {"state": "INSTALLED"}}, .2)
        self.assertEqual(result["state"], "PARTIAL")
        self.assertEqual(result["components"]["Voice"], "DISABLED")
        self.assertIsNone(result["boot_to_usable_seconds"])

    def test_only_observed_ready_components_allow_full_readiness(self):
        snapshot, rows = self.ready()
        self.assertEqual(project_readiness(snapshot, rows)["state"], "FULLY_READY")
        snapshot["voice"]["privacy_mode"] = True
        self.assertEqual(project_readiness(snapshot, rows)["components"]["Voice"], "MUTED")

    def test_old_ready_labels_expire(self):
        snapshot, rows = self.ready()
        for row in rows.values(): row["checked_at"] -= 120
        result = project_readiness(snapshot, rows)
        self.assertEqual(result["state"], "PARTIAL")
        self.assertEqual(result["components"]["Local AI"], "STALE")
        self.assertEqual(result["components"]["Remote"], "STALE")

    def test_current_worker_failure_overrides_cached_success(self):
        snapshot, rows = self.ready()
        snapshot["health"] = {"Local AI": {"state": "FAILED", "observed_at": time.time()}}
        self.assertEqual(project_readiness(snapshot, rows)["components"]["Local AI"], "FAILED")

    def test_fresh_worker_evidence_updates_without_remote_probe(self):
        snapshot, rows = self.ready()
        snapshot["health"] = {"Local AI": {"state": "READY", "observed_at": time.time()}}
        rows["LocalAI"] = {"state": "UNVERIFIED"}
        self.assertEqual(project_readiness(snapshot, rows)["components"]["Local AI"], "READY")

    def test_missing_invalid_and_future_timestamps_are_not_ready(self):
        snapshot, rows = self.ready()
        for stamp in [None, "today", float("nan"), float("inf"), True, time.time() + 30]:
            with self.subTest(stamp=stamp):
                rows["LocalAI"]["checked_at"] = stamp
                self.assertNotEqual(project_readiness(snapshot, rows)["components"]["Local AI"], "READY")

    def test_paused_and_resource_suspended_voice_are_distinct(self):
        snapshot, rows = self.ready()
        snapshot["voice"]["wake_paused"] = True
        self.assertEqual(project_readiness(snapshot, rows)["components"]["Voice"], "PAUSED")
        snapshot["voice"]["wake_paused"] = False
        snapshot["voice"]["resource_suspended"] = True
        self.assertEqual(project_readiness(snapshot, rows)["components"]["Voice"], "SUSPENDED")

    def test_worker_failure_overrides_adapter_availability(self):
        for name in ('VAD', 'STT', 'TTS'):
            snapshot, rows = self.ready()
            snapshot['health'][name]['state'] = 'FAILED'
            result = project_readiness(snapshot, rows)
            self.assertEqual(result['components'][name], 'FAILED')
            self.assertEqual(result['components']['Voice'], 'DEGRADED')
            self.assertEqual(result['state'], 'PARTIAL')

    def test_dead_process_overrides_fresh_ready_health_without_waiting_for_next_probe(self):
        for name in ('VAD', 'STT', 'TTS'):
            snapshot, rows = self.ready()
            snapshot['voice']['workers'][name]['running'] = False
            result = project_readiness(snapshot, rows)
            self.assertEqual(result['components'][name], 'STOPPED')
            self.assertEqual(result['components']['Voice'], 'DEGRADED')

    def test_missing_old_and_pre_resume_worker_evidence_is_not_ready(self):
        for name in ('VAD', 'STT', 'TTS'):
            for stamp in (None, time.time() - 120, time.time() - 1):
                snapshot, rows = self.ready()
                snapshot['health'][name]['observed_at'] = stamp
                result = project_readiness(snapshot, rows, fresh_after=time.time())
                self.assertNotEqual(result['components'][name], 'READY')
                self.assertNotEqual(result['components']['Voice'], 'READY')

    def test_on_demand_and_disabled_components_do_not_claim_live_workers(self):
        snapshot, rows = self.ready()
        snapshot['voice']['workers'] = {
            'VAD': {'mode': 'DISABLED', 'running': False},
            'STT': {'mode': 'ON_DEMAND', 'running': False, 'available': True},
            'TTS': {'mode': 'ON_DEMAND', 'running': False, 'available': True},
        }
        result = project_readiness(snapshot, rows)
        self.assertEqual(result['components']['VAD'], 'DISABLED')
        self.assertEqual(result['components']['STT'], 'ON_DEMAND')
        self.assertEqual(result['components']['TTS'], 'ON_DEMAND')
        self.assertEqual(result['components']['Voice'], 'READY')
        snapshot['voice']['workers']['TTS']['available'] = False
        self.assertEqual(project_readiness(snapshot, rows)['components']['Voice'], 'DEGRADED')

    def test_invalid_runtime_evidence_is_unknown_and_does_not_crash_projection(self):
        for value in (None, [], 'READY', True):
            snapshot, rows = self.ready()
            snapshot['voice']['workers'] = value
            self.assertEqual(project_readiness(snapshot, rows)['components']['Voice'], 'DEGRADED')
            snapshot['voice']['workers'] = {'VAD': value, 'STT': value, 'TTS': value}
            self.assertEqual(project_readiness(snapshot, rows)['components']['Voice'], 'DEGRADED')
            snapshot['health'] = value
            self.assertEqual(project_readiness(snapshot, rows)['components']['STT'], 'UNVERIFIED')
