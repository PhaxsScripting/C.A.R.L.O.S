import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from ev.events import PhaxEventBus
from ev.settings_center import SettingsCenter
from ev.tools.base import ValidationError


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name)
        self.data = {"voice": {"wake": {"enabled": False}}, "unrelated": {"keep": 123}}
        (path / "config.json").write_text(json.dumps(self.data))
        self.core = SimpleNamespace(
            config=self.data,
            paths=SimpleNamespace(config_dir=path, config_file=path / "config.json"),
            bus=PhaxEventBus(),
            privacy=SimpleNamespace(ephemeral=False, mode="NORMAL"),
            voice=SimpleNamespace(
                wake_desired=False,
                privacy_mode=False,
                snapshot=lambda: {"wake_active": False},
                set_wake_paused=AsyncMock(),
            ),
        )
        self.settings = SettingsCenter(self.core)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_wake_enable_updates_desired_and_persists_without_unrelated_changes(self):
        result = await self.settings.update({"key": "wake_enabled", "value": True}, None)
        self.assertTrue(result["verified"])
        self.assertTrue(self.core.voice.wake_desired)
        self.assertFalse(result["wake_active"])
        self.core.voice.set_wake_paused.assert_awaited_once_with(False)
        saved = json.loads(self.core.paths.config_file.read_text())
        self.assertEqual(saved["unrelated"], {"keep": 123})
        self.assertTrue(saved["voice"]["wake"]["enabled"])

    async def test_hard_mute_and_private_modes_prevent_persistent_enable(self):
        for private, muted in [(False, True), (True, False)]:
            self.core.privacy.ephemeral = private
            self.core.voice.privacy_mode = muted
            with self.assertRaises(ValidationError):
                await self.settings.update({"key": "wake_enabled", "value": True}, None)
        self.assertFalse(
            json.loads(self.core.paths.config_file.read_text())["voice"]["wake"]["enabled"]
        )

    async def test_unknown_keys_and_wrong_types_never_reach_disk(self):
        for key, value in [("approval_mode", False), ("spoken_replies", "false")]:
            with self.assertRaises(ValidationError):
                await self.settings.update({"key": key, "value": value}, None)
        self.assertEqual(json.loads(self.core.paths.config_file.read_text()), self.data)

    async def test_echo_changes_restart_only_owned_wake_path(self):
        self.core.voice.wake_paused = True
        self.core.voice.echo = SimpleNamespace(enabled=False, close=AsyncMock())
        result = await self.settings.update({"key": "echo_cancel", "value": True}, None)
        self.assertTrue(result["verified"])
        self.assertTrue(self.core.voice.echo.enabled)
        self.core.voice.echo.close.assert_awaited_once()
        self.assertEqual(
            [c.args for c in self.core.voice.set_wake_paused.await_args_list], [(True,), (True,)]
        )

    async def test_echo_change_during_capture_is_rejected_before_persistence(self):
        self.core.voice.capture_active = True
        with self.assertRaises(ValidationError):
            await self.settings.update({"key": "echo_cancel", "value": True}, None)
        self.assertNotIn(
            "echo_cancel", json.loads(self.core.paths.config_file.read_text())["voice"]
        )

    async def test_failed_wake_change_restores_disk_and_desired_state(self):
        self.core.voice.set_wake_paused.side_effect = [RuntimeError("capture failed"), None]
        with self.assertRaisesRegex(RuntimeError, "capture failed"):
            await self.settings.update({"key": "wake_enabled", "value": True}, None)
        self.assertFalse(self.core.voice.wake_desired)
        self.assertFalse(self.core.config["voice"]["wake"]["enabled"])
        self.assertEqual(
            json.loads(self.core.paths.config_file.read_text())["voice"]["wake"], {"enabled": False}
        )
        self.assertEqual(self.core.voice.set_wake_paused.await_count, 2)

    async def test_choices_validate_before_saving_and_keep_strict_types(self):
        from ev.tools.base import validate_schema
        validate_schema({"value": 300}, {"type": "object", "properties": {"value": {"type": ["boolean", "string", "integer"]}}})
        for key, value in [("operating_mode", "ROOT"), ("greeting_away_seconds", True),
                           ("greeting_away_seconds", 300.0), ("greeting_away_seconds", -1)]:
            with self.assertRaises(ValidationError):
                await self.settings.update({"key": key, "value": value}, None)
        self.assertNotIn("presence", json.loads(self.core.paths.config_file.read_text()))
        result = await self.settings.update({"key": "greeting_away_seconds", "value": 60}, None)
        self.assertTrue(result["verified"])
        self.assertEqual(self.core.config["presence"]["away_seconds"], 60)

    async def test_mode_changes_log_level_and_survives_reload(self):
        import logging
        self.core.logger = logging.Logger("settings-test")
        for mode, level in [("DEV", logging.DEBUG), ("DAILY", logging.INFO)]:
            result = await self.settings.update({"key": "operating_mode", "value": mode}, None)
            self.assertTrue(result["verified"])
            self.assertEqual(self.core.logger.level, level)
            self.core.config = json.loads(self.core.paths.config_file.read_text())
            fields = {f["key"]: f for f in self.settings.snapshot()["choices"]}
            self.assertEqual(fields["operating_mode"]["value"], mode)
            self.assertFalse(self.core.voice.wake_desired)

    async def test_private_session_cannot_persist_mode(self):
        self.core.privacy.ephemeral = True
        with self.assertRaises(ValidationError):
            await self.settings.update({"key": "operating_mode", "value": "DEV"}, None)
        self.assertNotIn("carlos", json.loads(self.core.paths.config_file.read_text()))
