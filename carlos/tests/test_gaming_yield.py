import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import psutil

from ev.events import PhaxEventBus
from ev.gaming import GamingPolicy, minecraft_clients
from ev.state import StateMachine
from ev.voice.manager import VoiceManager


class GamingYieldTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bus = PhaxEventBus()
        self.voice = VoiceManager({"capture_command": ["/missing"]}, self.bus, StateMachine(self.bus))
        self.voice._refresh_wake_supervisor = AsyncMock()
        self.core = SimpleNamespace(config={}, voice=self.voice)

    async def test_game_yield_is_separate_from_manual_pause_and_does_not_change_preferences(self):
        self.voice.wake_desired = True
        policy = GamingPolicy(self.core, lambda: {"clients": [(123, 45)], "complete": True})
        await policy.refresh()
        self.assertTrue(self.voice.gaming_suspended)
        self.assertFalse(self.voice.wake_paused)
        self.assertTrue(self.voice.wake_desired)
        self.assertEqual(policy.state, "YIELDING")
        await self.voice.set_wake_paused(True)
        policy.probe = lambda: {"clients": [], "complete": True}
        await policy.refresh()
        self.assertTrue(self.voice.gaming_suspended)
        await policy.refresh()
        self.assertFalse(self.voice.gaming_suspended)
        self.assertTrue(self.voice.wake_paused)
        self.assertTrue(self.voice.wake_desired)

    async def test_incomplete_or_failed_inventory_does_not_resume_ambient_listening(self):
        self.voice.gaming_suspended = True
        policy = GamingPolicy(self.core, lambda: {"clients": [], "complete": False})
        await policy.refresh()
        self.assertTrue(self.voice.gaming_suspended)
        self.assertEqual(policy.state, "UNKNOWN")
        policy.probe = Mock(side_effect=OSError("fixture"))
        await policy.refresh()
        self.assertTrue(self.voice.gaming_suspended)
        self.voice._refresh_wake_supervisor.assert_not_awaited()

    async def test_one_missing_sample_cannot_resume_and_reappearing_game_resets_debounce(self):
        policy = GamingPolicy(self.core, lambda: {"clients": [(123, 45)], "complete": True})
        await policy.refresh()
        policy.probe = lambda: {"clients": [], "complete": True}
        await policy.refresh()
        self.assertTrue(self.voice.gaming_suspended)
        policy.probe = lambda: {"clients": [(456, 67)], "complete": True}
        await policy.refresh()
        self.assertEqual(policy.empty_observations, 0)
        self.assertEqual(self.voice._refresh_wake_supervisor.await_count, 2)

    async def test_disabled_policy_releases_only_its_own_gate(self):
        self.voice.gaming_suspended = True
        self.voice.privacy_mode = True
        self.core.config = {"resources": {"yield_ambient_for_minecraft": False}}
        probe = Mock()
        policy = GamingPolicy(self.core, probe)
        await policy.refresh()
        probe.assert_not_called()
        self.assertFalse(self.voice.gaming_suspended)
        self.assertTrue(self.voice.privacy_mode)

    async def test_game_skips_ambient_model_prewarm_and_speech_backup(self):
        self.voice.gaming_suspended = True
        self.voice.wake_desired = True
        self.voice.config["wake"] = {"speech_backup": True}
        for owner in (self.voice.stt, self.voice.tts, self.voice.preview_stt):
            owner.prewarm = AsyncMock()
        self.voice.neural_vad.start = AsyncMock()
        await self.voice.prewarm()
        self.voice.stt.prewarm.assert_not_awaited()
        self.voice.tts.prewarm.assert_not_awaited()
        self.voice.preview_stt.prewarm.assert_not_awaited()
        self.voice.neural_vad.start.assert_not_awaited()
        self.assertFalse(self.voice._speech_wake_allowed())

    async def test_real_wake_supervisor_is_cancelled_and_no_restart_happens_during_game(self):
        self.voice._refresh_wake_supervisor = VoiceManager._refresh_wake_supervisor.__get__(self.voice)
        self.voice.wake_desired = True
        started = asyncio.Event()
        async def waiting():
            started.set()
            await asyncio.sleep(60)
        self.voice._wake_supervisor = waiting
        self.voice._stop_wake_runtime = AsyncMock()
        await self.voice._refresh_wake_supervisor()
        await started.wait()
        child = self.voice.wake_supervisor_task
        await self.voice.set_gaming_suspended(True)
        self.assertTrue(child.done())
        self.assertIsNone(self.voice.wake_supervisor_task)
        self.voice._stop_wake_runtime.assert_awaited_once()
        await self.voice.set_wake_paused(False)
        self.assertIsNone(self.voice.wake_supervisor_task)
        self.assertEqual(self.voice.diagnostics["wake_state"], "GAMING")
        self.voice.privacy_mode = True
        await self.voice.set_gaming_suspended(False)
        self.assertIsNone(self.voice.wake_supervisor_task)
        self.assertEqual(self.voice.diagnostics["wake_state"], "PRIVATE")

    def test_inventory_requires_owned_running_java_client_directory_and_options(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "minecraft"
            directory.mkdir()
            (directory / "options.txt").write_text("renderDistance:8\n")
            process = Mock(pid=123, info={"name": "java"})
            process.uids.return_value = SimpleNamespace(effective=os.getuid())
            process.create_time.return_value = 45
            process.cwd.return_value = str(directory)
            process.status.return_value = psutil.STATUS_RUNNING
            with patch("ev.gaming.psutil.process_iter", return_value=[process]), patch(
                "ev.gaming.psutil.Process", return_value=process
            ):
                self.assertEqual(minecraft_clients()["clients"], [(123, 45)])
                process.uids.return_value = SimpleNamespace(effective=os.getuid() + 1)
                self.assertFalse(minecraft_clients()["clients"])
                process.uids.return_value = SimpleNamespace(effective=os.getuid())
                process.status.return_value = psutil.STATUS_ZOMBIE
                self.assertFalse(minecraft_clients()["clients"])
                process.status.return_value = psutil.STATUS_RUNNING
                options = directory / "options.txt"
                options.unlink()
                options.symlink_to(directory / "missing")
                self.assertFalse(minecraft_clients()["clients"])

    def test_pid_reuse_and_unavailable_java_identity_leave_inventory_incomplete(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "minecraft"
            directory.mkdir()
            (directory / "options.txt").write_text("renderDistance:8\n")
            process = Mock(pid=123, info={"name": "java"})
            process.uids.return_value = SimpleNamespace(effective=os.getuid())
            process.create_time.return_value = 45
            process.cwd.return_value = str(directory)
            process.status.return_value = psutil.STATUS_RUNNING
            other = Mock()
            other.create_time.return_value = 46
            with patch("ev.gaming.psutil.process_iter", return_value=[process]), patch(
                "ev.gaming.psutil.Process", return_value=other
            ):
                result = minecraft_clients()
                self.assertFalse(result["complete"])
                self.assertFalse(result["clients"])
                process.cwd.side_effect = psutil.AccessDenied(123)
                self.assertFalse(minecraft_clients()["complete"])
