import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ev.service import CarlosCore


class PetPanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_panel_reports_privacy_without_conversation_or_window_content(self):
        for mode, muted, expected in [
            ("NORMAL", False, False),
            ("NORMAL", True, True),
            ("PRIVATE SESSION", False, True),
            ("LOCAL ONLY", False, True),
            ("GUEST", False, True),
        ]:
            core = CarlosCore.__new__(CarlosCore)
            core.voice = SimpleNamespace(snapshot=Mock(return_value={"privacy_mode": muted}))
            core.privacy = SimpleNamespace(mode=mode, changing=False)
            core.state = SimpleNamespace(current=SimpleNamespace(value="DORMANT"), detail="idle")
            result = await core.handle_request({"type": "panel.state", "payload": {}})
            if mode == "GUEST":
                self.assertEqual(result["status"], "denied")
            else:
                self.assertEqual(result["privacy_mode"], expected)
            self.assertNotIn("conversation", result)
            self.assertNotIn("windows", result)

    async def test_transition_hides_pet_before_mode_is_committed(self):
        core = CarlosCore.__new__(CarlosCore)
        core.voice = SimpleNamespace(snapshot=Mock(return_value={}))
        core.privacy = SimpleNamespace(mode='NORMAL', changing=True)
        core.state = SimpleNamespace(current=SimpleNamespace(value='DORMANT'), detail='idle')
        self.assertTrue((await core.handle_request({'type':'panel.state','payload':{}}))['privacy_mode'])

    async def test_summary_contains_only_privacy_activity_and_verified_session_lock(self):
        core = CarlosCore.__new__(CarlosCore)
        core.voice = SimpleNamespace(snapshot=Mock(return_value={'waveform':[.4], 'microphone':'device_canary'}))
        core.privacy = SimpleNamespace(mode='NORMAL', changing=False)
        core.state = SimpleNamespace(current=SimpleNamespace(value='DORMANT'), detail='request_canary')
        for session, locked in [('UNLOCKED',False),('LOCKED',True),('UNKNOWN',True),(None,True)]:
            core.presence = SimpleNamespace(state={'session':session})
            result = await core.handle_request({'type':'panel.summary','payload':{}})
            self.assertEqual(result,{'privacy_mode':False,'state':'DORMANT','session_locked':locked})
