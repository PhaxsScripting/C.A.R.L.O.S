import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from ev.tools.holohand import exchange, status, set_paused
from ev.tools.base import ValidationError


class HoloHandControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "holohand.sock"
        self.commands = []
        self.state = "READY"
        self.oversize = False
        self.extra = ""
        self.writers = []

        async def respond(reader, writer):
            self.writers.append(writer)
            cmd = (await reader.read(1024)).decode()
            self.commands.append(cmd)
            if cmd == "--pause":
                self.state = "PAUSED"
            if cmd == "--resume":
                self.state = "READY"
            reply = "x" * 8193 if self.oversize else self.state + " | input ready | idle\n" + self.extra
            writer.write(reply.encode())
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        self.server = await asyncio.start_unix_server(respond, path=self.path)
        self.patch = patch("ev.tools.holohand.socket_path", return_value=self.path)
        self.patch.start()

    async def asyncTearDown(self):
        self.patch.stop()
        self.server.close()
        await self.server.wait_closed()
        for writer in self.writers:
            writer.close()
        self.temp.cleanup()

    async def test_counters_and_peer_identity_are_metadata_only(self):
        self.extra = "Inference counters: inferred 80; skipped 20\nCapture counter: 100\n"
        result = await exchange("--status")
        self.assertEqual(result['counters'],{'captured':100,'inferred':80,'skipped':20})
        self.assertEqual(result['peer_pid'],os.getpid())
        self.assertIsInstance(result['peer_start_ticks'],int)
        self.assertFalse(result['camera_started'])
        self.assertEqual(self.commands,['--status'])

    async def test_old_or_invalid_counter_protocol_has_no_fabricated_rates(self):
        self.assertEqual((await exchange("--status"))['counters'],{})

    async def test_idle_pipeline_does_not_present_old_tracking_as_live(self):
        self.extra = 'Pipeline demand: IDLE\nHand: visible; confidence 0.98; inference 12 ms; age 90000 ms;\n'
        result = await exchange('--status')
        self.assertEqual(result['pipeline_demand'], 'IDLE')
        self.assertEqual(result['tracking'], {'hand_visible': False})
        self.extra = 'Pipeline demand: ACTIVE\nHand: not detected; confidence 0.00; inference 12 ms; age 30 ms;\n'
        result = await exchange('--status')
        self.assertEqual(result['pipeline_demand'], 'ACTIVE')
        self.assertEqual(result['tracking']['inference_ms'], 12)
        self.extra = ''
        self.assertIsNone((await exchange('--status'))['pipeline_demand'])
        self.extra = "Inference counters: inferred 10; skipped 0\nCapture counter: 9999999999999999999999999999\n"
        self.assertEqual((await exchange("--status"))['counters'],{})

    async def test_pause_and_resume_have_fresh_status_readback(self):
        result = await set_paused({"paused": True}, None)
        self.assertTrue(result["verified"])
        self.assertEqual(self.commands, ["--pause", "--status"])
        result = await set_paused({"paused": False}, None)
        self.assertTrue(result["verified"])
        self.assertFalse(result["camera_started"])

    async def test_missing_socket_does_not_launch_anything(self):
        with patch("ev.tools.holohand.socket_path", return_value=self.path.with_name("missing")):
            self.assertFalse((await status({}, None))["available"])
        self.assertEqual(self.commands, [])

    async def test_unsafe_directory_rejected_before_sending(self):
        os.chmod(self.temp.name, 0o777)
        try:
            with self.assertRaises(ValidationError):
                await exchange("--resume")
        finally:
            os.chmod(self.temp.name, 0o700)
        self.assertEqual(self.commands, [])

    async def test_oversized_response_rejected(self):
        self.oversize = True
        with self.assertRaises(ValidationError):
            await exchange("--status")


    async def test_swipe_setting_is_exact_optional_metadata_without_enabling_input(self):
        for text, expected in [("", None), ("Swipe: enabled\n", True), ("Swipe: disabled\n", False), ("Swipe: maybe\n", None)]:
            self.extra = text
            self.assertIs((await exchange("--status"))["swipe_enabled"], expected)
        self.assertEqual(self.commands, ["--status"] * 4)

    async def test_pause_readback_never_verifies_a_replacement_or_unknown_instance(self):
        first = dict(available=True, state="READY", peer_pid=123, peer_start_ticks=456, camera_started=False)
        for changed in ({"peer_pid": 124}, {"peer_start_ticks": 457}, {"peer_pid": None}, {"peer_start_ticks": None}):
            after = dict(first, state="PAUSED", **changed)
            with patch("ev.tools.holohand.exchange", new=AsyncMock(side_effect=[first, after])) as ipc:
                result = await set_paused({"paused": True}, None)
                self.assertFalse(result["verified"])
                self.assertFalse(result["instance_identity_verified"])
                self.assertEqual([call.args[0] for call in ipc.await_args_list], ["--pause", "--status"])

    async def test_status_and_pause_contracts_validate_actual_owned_socket_results(self):
        import logging
        from ev.events import PhaxEventBus
        from ev.tools.base import ToolContext, ToolRegistry, validate_schema
        from ev.tools.holohand import register_holohand_tools
        registry = ToolRegistry(ToolContext({}, PhaxEventBus(), logging.getLogger("holo-contract")))
        register_holohand_tools(registry)
        for name in ("holohand.status", "holohand.set_paused"):
            self.assertFalse(registry.get(name).public()["contract_gaps"])
        self.extra = "Swipe: disabled\n"
        validate_schema(await status({}, None), registry.get("holohand.status").output_schema)
        result = await set_paused({"paused": True}, None)
        self.assertTrue(result["instance_identity_verified"])
        validate_schema(result, registry.get("holohand.set_paused").output_schema)
        with patch("ev.tools.holohand.socket_path", return_value=self.path.with_name("missing")):
            validate_schema(await status({}, None), registry.get("holohand.status").output_schema)
