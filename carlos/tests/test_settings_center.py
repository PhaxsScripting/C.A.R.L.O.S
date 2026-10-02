import json
import asyncio
import logging
import time
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

    async def test_ducking_is_opt_in_and_persists_without_output_changes(self):
        fields = {field['key']: field for field in self.settings.snapshot()['fields']}
        self.assertFalse(fields['media_ducking']['value'])
        result = await self.settings.update({'key': 'media_ducking', 'value': True}, None)
        self.assertTrue(result['verified'])
        saved = json.loads(self.core.paths.config_file.read_text())
        self.assertTrue(saved['voice']['tts']['duck_media'])
        self.assertNotIn('output_device', saved['voice']['tts'])
        self.core.voice.set_wake_paused.assert_not_awaited()

    async def test_security_monitor_can_be_disabled_without_voice_or_approval_changes(self):
        result = await self.settings.update({'key': 'security_monitoring', 'value': False}, None)
        self.assertTrue(result['verified'])
        saved = json.loads(self.core.paths.config_file.read_text())
        self.assertFalse(saved['security']['monitoring']['enabled'])
        self.assertNotIn('approval_mode', saved['security'])
        self.core.voice.set_wake_paused.assert_not_awaited()

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


    async def test_undo_absent_field_preserves_unrelated_external_edits_and_empty_sections(self):
        self.data["voice"]["tts"] = {}
        self.core.paths.config_file.write_text(json.dumps(self.data))
        await self.settings.update({"key": "media_ducking", "value": True}, None)
        data = json.loads(self.core.paths.config_file.read_text())
        data["unrelated"]["keep"] = 456
        data["private_token"] = "not part of undo"
        self.core.paths.config_file.write_text(json.dumps(data))
        result = await self.settings.undo({}, None)
        self.assertTrue(result["verified"])
        self.assertFalse(result["value"])
        saved = json.loads(self.core.paths.config_file.read_text())
        self.assertEqual(saved["voice"]["tts"], {})
        self.assertEqual(saved["unrelated"]["keep"], 456)
        self.assertEqual(saved["private_token"], "not part of undo")
        self.assertEqual(self.core.config["voice"]["tts"], {})
        self.assertFalse(self.settings.snapshot()["undo_available"])

    async def test_undo_noop_does_not_hide_last_change_and_guard_does_not_skip_history(self):
        await self.settings.update({"key": "media_ducking", "value": True}, None)
        await self.settings.update({"key": "media_ducking", "value": True}, None)
        self.assertEqual(len(self.settings.history), 1)
        refused = await self.settings.undo({"key": "spoken_replies"}, None)
        self.assertFalse(refused["verified"])
        self.assertEqual(len(self.settings.history), 1)
        self.assertTrue((await self.settings.undo({"key": "media_ducking"}, None))["verified"])
        self.assertNotIn("tts", json.loads(self.core.paths.config_file.read_text())["voice"])

    async def test_undo_rejects_external_disk_or_runtime_changes_without_overwriting(self):
        for runtime in (False, True):
            await self.settings.update({"key": "media_ducking", "value": True}, None)
            if runtime:
                self.core.config["voice"]["tts"]["duck_media"] = False
            else:
                data = json.loads(self.core.paths.config_file.read_text())
                data["voice"]["tts"]["duck_media"] = False
                self.core.paths.config_file.write_text(json.dumps(data))
            disk = self.core.paths.config_file.read_bytes()
            result = await self.settings.undo({}, None)
            self.assertFalse(result["restored"])
            self.assertEqual(self.core.paths.config_file.read_bytes(), disk)
            self.assertFalse(self.settings.history)

    async def test_undo_restores_choice_logger_and_wake_without_claiming_capture(self):
        self.core.logger = logging.Logger("settings-undo")
        self.core.voice.wake_paused = True
        await self.settings.update({"key": "operating_mode", "value": "DEV"}, None)
        self.assertTrue((await self.settings.undo({}, None))["verified"])
        self.assertEqual(self.core.logger.level, logging.INFO)
        await self.settings.update({"key": "wake_enabled", "value": True}, None)
        result = await self.settings.undo({}, None)
        self.assertTrue(result["verified"])
        self.assertFalse(result["wake_active"])
        self.assertFalse(self.core.voice.wake_desired)
        self.core.voice.set_wake_paused.assert_awaited_with(True)

    async def test_undo_failed_voice_apply_keeps_record_and_restores_changed_field(self):
        await self.settings.update({"key": "wake_enabled", "value": True}, None)
        self.core.voice.set_wake_paused.side_effect = [RuntimeError("owned capture failed"), None]
        with self.assertRaisesRegex(RuntimeError, "owned capture failed"):
            await self.settings.undo({}, None)
        self.assertEqual(len(self.settings.history), 1)
        self.assertTrue(self.core.voice.wake_desired)
        self.assertTrue(json.loads(self.core.paths.config_file.read_text())["voice"]["wake"]["enabled"])
        self.core.voice.set_wake_paused.side_effect = None
        self.assertTrue((await self.settings.undo({}, None))["verified"])

    async def test_cancelled_voice_apply_rolls_back_only_its_field(self):
        entered = asyncio.Event()
        async def delayed(paused):
            if not paused:
                data = json.loads(self.core.paths.config_file.read_text())
                data["unrelated"]["keep"] = 789
                self.core.paths.config_file.write_text(json.dumps(data))
                entered.set()
                await asyncio.Event().wait()
        self.core.voice.set_wake_paused.side_effect = delayed
        task = asyncio.create_task(self.settings.update({"key": "wake_enabled", "value": True}, None))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        saved = json.loads(self.core.paths.config_file.read_text())
        self.assertFalse(saved["voice"]["wake"]["enabled"])
        self.assertEqual(saved["unrelated"]["keep"], 789)
        self.assertFalse(self.settings.history)
        self.assertFalse(self.core.voice.wake_desired)

    async def test_external_same_field_edit_during_voice_apply_is_not_rolled_back(self):
        async def changed(paused):
            if not paused:
                data = json.loads(self.core.paths.config_file.read_text())
                data["voice"]["wake"]["enabled"] = "external edit"
                self.core.paths.config_file.write_text(json.dumps(data))
        self.core.voice.set_wake_paused.side_effect = changed
        with self.assertRaisesRegex(ValidationError, "before verification"):
            await self.settings.update({"key": "wake_enabled", "value": True}, None)
        self.assertEqual(json.loads(self.core.paths.config_file.read_text())["voice"]["wake"]["enabled"], "external edit")
        self.assertFalse(self.settings.history)

    async def test_waiting_request_rechecks_stop_and_private_policy(self):
        for private in (True, False):
            await self.settings.lock.acquire()
            task = asyncio.create_task(self.settings.update({"key": "media_ducking", "value": True}, None))
            await asyncio.sleep(0)
            if private:
                self.core.privacy.ephemeral = True
            else:
                self.core._action_generation = 1
            self.settings.lock.release()
            with self.assertRaises(ValidationError):
                await task
            self.core.privacy.ephemeral = False
        self.assertNotIn("tts", json.loads(self.core.paths.config_file.read_text())["voice"])

    async def test_privacy_transition_during_apply_never_restarts_old_wake(self):
        self.core.voice.wake_paused = False
        async def change(paused):
            if not paused:
                self.core.privacy.mode = "DO NOT LISTEN"
                self.core.voice.privacy_mode = True
        self.core.voice.set_wake_paused.side_effect = change
        with self.assertRaises(ValidationError):
            await self.settings.update({"key": "wake_enabled", "value": True}, None)
        self.core.voice.set_wake_paused.assert_awaited_with(True)
        self.assertFalse(self.settings.history)

    async def test_private_undo_refuses_and_history_is_bounded_and_expires(self):
        for index in range(40):
            await self.settings.update({"key": "greeting_away_seconds", "value": 60 if index % 2 else 900}, None)
        self.assertEqual(len(self.settings.history), 32)
        self.core.privacy.ephemeral = True
        with self.assertRaises(ValidationError):
            await self.settings.undo({}, None)
        self.core.privacy.ephemeral = False
        for entry in self.settings.history:
            entry["expires"] = time.monotonic() - 1
        self.assertFalse((await self.settings.undo({}, None))["verified"])
        self.assertFalse(self.settings.history)

    async def test_config_symlink_and_concurrent_replacement_refuse_before_write(self):
        config = self.core.paths.config_file
        other = config.with_name("other.json")
        original = config.read_bytes()
        other.write_bytes(original)
        config.unlink()
        config.symlink_to(other)
        with self.assertRaises(ValidationError):
            await self.settings.update({"key": "media_ducking", "value": True}, None)
        self.assertEqual(other.read_bytes(), original)
        config.unlink()
        config.write_bytes(original)
        data, identity = self.settings._read()
        config.write_text(json.dumps({"external": True}))
        with self.assertRaisesRegex(ValidationError, "before save"):
            self.settings._save(data, identity)
        self.assertEqual(json.loads(config.read_text()), {"external": True})

    def test_settings_contracts_validate_real_outputs_and_privacy_clears_history(self):
        from ev.tools.base import ToolContext, ToolRegistry, validate_schema
        from ev.privacy import PrivacyPolicy
        registry = ToolRegistry(ToolContext({}, self.core.bus, logging.getLogger("settings-contract")))
        self.settings.register(registry)
        for name in ("carlos.settings.get", "carlos.settings.set", "carlos.settings.undo_last"):
            self.assertFalse(registry.get(name).public()["contract_gaps"])
        before = json.dumps(self.core.config, sort_keys=True)
        validate_schema(self.settings.snapshot(), registry.get("carlos.settings.get").output_schema)
        self.assertEqual(json.dumps(self.core.config, sort_keys=True), before)
        self.settings.history.append({"expires": time.monotonic() + 600})
        from unittest.mock import Mock
        self.core.settings_center = self.settings
        for name, method in (("memory", "set_private"), ("task_journal", "set_private"), ("daily", "set_private")):
            setattr(self.core, name, SimpleNamespace(**{method: Mock()}))
        self.core.logger = logging.Logger("settings-privacy")
        PrivacyPolicy(self.core).apply_storage()
        self.assertFalse(self.settings.history)

    def test_exact_undo_command_never_uses_quoted_or_negated_text(self):
        from ev.commands import direct_action
        action = direct_action("undo my last Carlos setting change")
        self.assertEqual(action.tool, "carlos.settings.undo_last")
        for phrase in ('do not undo my last Carlos setting change', 'say "undo my last Carlos setting change"'):
            action = direct_action(phrase)
            self.assertTrue(action is None or action.tool != "carlos.settings.undo_last")


class SettingsIPCTests(unittest.IsolatedAsyncioTestCase):
    import test_tool_cancellation as fixtures
    setUp = fixtures.ToolCancellationTests.setUp
    asyncTearDown = fixtures.ToolCancellationTests.asyncTearDown
    connection = fixtures.ToolCancellationTests.connection
    send = fixtures.ToolCancellationTests.send
    reply = fixtures.ToolCancellationTests.reply

    async def test_real_core_socket_saves_restores_and_returns_same_client_health(self):
        await self.core.ipc.start()
        reader, writer = await self.connection()
        original = json.loads(self.core.paths.config_file.read_text())
        value = not original.get("voice", {}).get("tts", {}).get("duck_media", False)
        for ident, name, args in (
            ("set", "carlos.settings.set", {"key": "media_ducking", "value": value}),
            ("undo", "carlos.settings.undo_last", {"key": "media_ducking"}),
            ("get", "carlos.settings.get", {}),
        ):
            await self.send(writer, "tool.call", ident, {"name": name, "arguments": args})
            response = await self.reply(reader, ident)
            self.assertEqual(response["status"], "completed", response)
            if ident != "get":
                self.assertTrue(response["result"]["verified"])
                self.assertTrue(response["execution"]["verified"])
            if ident == "set":
                self.assertEqual(json.loads(self.core.paths.config_file.read_text())["voice"]["tts"]["duck_media"], value)
        self.assertEqual(json.loads(self.core.paths.config_file.read_text()), original)
        await self.send(writer, "health", "health", {})
        self.assertTrue((await self.reply(reader, "health"))["ok"])
