import asyncio
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from ev.events import PhaxEventBus
from ev.privacy import PrivacyPolicy
from ev.tools import ToolContext, ToolRegistry
from ev.tools.base import validate_schema
from ev.tools.local_vision import register_local_vision_tools
from ev.tools.results import evaluate_result
from ev.vision import ScreenPerception
from ev.vision_geometry import private_png, validate_element
from ev.desktop.input import DesktopInput
from ev.permissions import Permission, PermissionBroker
from ev.tools.base import ToolSpec
from ev.tools.builtin import object_schema


def label(x=10, y=10, text="Retry"):
    return {"text": text, "confidence": .95,
            "box": [[x, y], [x + 40, y], [x + 40, y + 15], [x, y + 15]],
            "center": {"x": x + 20, "y": y + 7.5}}


class VisualTargetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.window = {
            "id": "exact", "pid": os.getpid(), "title": "Owned fixture", "app_id": "fixture",
            "normal": True, "deleted": False, "minimized": False,
            "geometry": {"x": 10, "y": 20, "width": 120, "height": 80},
            "output": "fixture-screen", "desktops": ["desk"], "maximize_mode": 0,
        }
        self.output = {"name": "fixture-screen", "scale": 1, "transform": 0,
                       "geometry": {"x": 0, "y": 0, "width": 300, "height": 200}}
        self.world = {"windows": [self.window], "outputs": [self.output],
                      "current_desktop": "desk", "active_window_id": "exact", "cursor": {"x": 40, "y": 37.5}}
        self.window["fullscreen"] = False
        self.desktop = SimpleNamespace(snapshot=AsyncMock(side_effect=self.snapshot))
        self.desktop.bridge = SimpleNamespace(request=AsyncMock(side_effect=self.bridge_request))
        self.input_events = []
        self.portal = SimpleNamespace(notify=self.notify, _cancelled=threading.Event(),
                                      status=lambda: {"connected": True})
        self.desktop.input = DesktopInput(self.desktop, portal=self.portal)
        self.perception = ScreenPerception(self.root / "captures", self.desktop)
        self.targets = self.perception.visual_targets
        self.labels = [label()]
        self.perception.capture = AsyncMock(side_effect=self.capture)
        self.perception.ocr = AsyncMock(side_effect=self.ocr)
        self.context = ToolContext({"providers": {"active": "offline"},
                                    "security": {"max_tool_output_bytes": 65536}}, PhaxEventBus(),
                                   logging.getLogger("visual-fixture"), vision=self.perception)
        self.registry = ToolRegistry(self.context)
        register_local_vision_tools(self.registry)
        async def native_world(arguments, context):
            return await self.snapshot()
        self.registry.register(ToolSpec("desktop.world", "DESKTOP", "Fixture native observation",
                                      Permission.SAFE, object_schema({}, []),
                                      native_world, read_only=True))

    async def bridge_request(self, action, arguments, **kwargs):
        if action == "activate":
            self.world["active_window_id"] = arguments["window_id"]
            return {"requested": True}
        if action == "snapshot":
            return await self.snapshot()
        raise ValueError("Unsupported fixture bridge request")

    def notify(self, method, *arguments):
        self.input_events.append((method, arguments))
        if method == "NotifyPointerMotion":
            self.world["cursor"]["x"] += arguments[0]
            self.world["cursor"]["y"] += arguments[1]
        elif method == "NotifyPointerButton" and arguments[-1] == 1:
            self.window["fullscreen"] = True

    async def observe(self, payload, correlation):
        self.assertEqual(payload, {"name": "desktop.world", "arguments": {}})
        return {"status": "completed", "result": await self.snapshot()}

    async def click_arguments(self):
        candidate = (await self.prepare())["candidates"][0]
        return {"candidate_id": candidate["candidate_id"], "expected": [
            {"kind": "window_state", "window_id": "exact", "property": "fullscreen", "expected": True}
        ]}

    async def snapshot(self, **kwargs):
        return deepcopy({**self.world, "captured_at_monotonic": time.monotonic()})

    async def capture(self, **kwargs):
        ident = os.urandom(16).hex()
        path = self.perception.capture_root / (ident + ".png")
        Image.new("RGB", (120, 80), "white").save(path)
        path.chmod(0o600)
        return {"capture_id": ident, "kind": "window", "sha256": private_png(path)[2]}

    async def ocr(self, ident, score):
        return {"elements": deepcopy(self.labels),
                "capture_sha256": private_png(self.perception.capture_root / (ident + ".png"))[2]}

    async def prepare(self):
        return await self.targets.prepare("exact", "retry")

    def test_box_validation_rejects_crossing_degenerate_outside_and_wrong_center(self):
        self.assertEqual(validate_element(label(), 120, 80, .8), label())
        variants = []
        for field, value in (("box", [[0, 0]] * 4),
                             ("box", [[0, 0], [20, 20], [20, 0], [0, 20]]),
                             ("box", [[-1, 0], [20, 0], [20, 20], [0, 20]]),
                             ("box", [[0, 0], [121, 0], [121, 20], [0, 20]]),
                             ("box", [[0, 0], [float("nan"), 0], [20, 20], [0, 20]]),
                             ("center", {"x": True, "y": 17.5}),
                             ("center", {"x": 31, "y": 17.5}),
                             ("confidence", float("inf")), ("text", ""), ("text", "x" * 4097)):
            variants.append({**label(), field: value})
        for element in variants:
            with self.subTest(element=element), self.assertRaises(ValueError):
                validate_element(element, 120, 80, .8)

    def test_private_png_rejects_links_permissions_and_non_png(self):
        path = self.root / "private.png"
        Image.new("RGB", (40, 20)).save(path)
        path.chmod(0o600)
        data, size, digest = private_png(path)
        self.assertEqual(size, (40, 20))
        self.assertEqual(digest, hashlib.sha256(data).hexdigest())
        link = self.root / "link.png"
        link.symlink_to(path)
        with self.assertRaises(OSError):
            private_png(link)
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            private_png(path)
        path.chmod(0o600)
        hard = self.root / "hard.png"
        os.link(path, hard)
        with self.assertRaises(ValueError):
            private_png(path)
        hard.unlink()
        path.write_bytes(b"not a PNG")
        with self.assertRaises(OSError):
            private_png(path)

    async def test_registered_candidates_produce_real_highlighted_private_previews_without_input(self):
        spec, arguments = self.registry.validate("vision.candidates", {"window_id": "exact", "text": "Retry"})
        result = await self.registry.execute(spec, arguments)
        self.assertEqual(result["matched"], 1)
        self.assertFalse(result["coordinate_actions_allowed"])
        candidate = result["candidates"][0]
        path = Path(candidate["preview_path"])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with Image.open(path) as preview:
            self.assertEqual(preview.getpixel((10, 10)), (255, 185, 71))
            self.assertEqual(preview.getpixel((80, 50)), (255, 255, 255))
        checked = await self.targets.review(candidate["candidate_id"])
        validate_schema(checked, self.registry.get("vision.candidate.review").output_schema)
        self.assertEqual(checked["candidate_id"], candidate["candidate_id"])
        for name, output in (("vision.candidates", result), ("vision.candidate.review", checked)):
            self.assertFalse(self.registry.get(name).public()["contract_gaps"])
            evidence = evaluate_result(name, output)
            self.assertTrue(evidence.ok)
            self.assertFalse(evidence.verified)
            self.assertEqual(evidence.scope, "visual_proposal")

    async def test_duplicate_labels_remain_separate_and_partial_text_does_not_match(self):
        self.labels = [label(), label(60, 40), label(10, 40, "Retry payment")]
        result = await self.prepare()
        self.assertTrue(result["ambiguous"])
        self.assertEqual(result["matched"], 2)
        self.assertEqual(len({c["candidate_id"] for c in result["candidates"]}), 2)
        for candidate in result["candidates"]:
            await self.targets.review(candidate["candidate_id"])

    async def test_no_match_and_cancellation_remove_owned_captures(self):
        self.labels = [label(text="Different")]
        self.assertEqual((await self.prepare())["matched"], 0)
        self.assertEqual(list(self.perception.capture_root.glob("*.png")), [])
        self.perception.ocr = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await self.prepare()
        self.assertEqual(list(self.perception.capture_root.glob("*.png")), [])

    async def test_replacement_geometry_output_and_desktop_changes_reject_and_cleanup(self):
        original = deepcopy(self.world)
        for change in (lambda: self.window.update(pid=99999999),
                       lambda: self.window["geometry"].update(x=11),
                       lambda: self.output.update(scale=2),
                       lambda: self.world.update(current_desktop="other")):
            with self.subTest(change=change):
                self.world = deepcopy(original)
                self.window, self.output = self.world["windows"][0], self.world["outputs"][0]
                candidate = (await self.prepare())["candidates"][0]
                change()
                with self.assertRaises((ValueError, OSError)):
                    await self.targets.review(candidate["candidate_id"])
                self.targets.clear()
                self.assertEqual(list(self.perception.capture_root.glob("*.png")), [])

    async def test_changed_capture_or_preview_rejected(self):
        for key in ("capture_id", "preview_id"):
            result = await self.prepare()
            candidate = result["candidates"][0]
            path = self.perception.capture_root / (candidate[key] + ".png")
            Image.new("RGB", (120, 80), "blue").save(path)
            with self.subTest(key=key), self.assertRaises(ValueError):
                await self.targets.review(candidate["candidate_id"])
            self.assertEqual(list(self.perception.capture_root.glob("*.png")), [])

    async def test_expiry_delete_and_privacy_revoke_candidates_and_files(self):
        for mode in ("expiry", "delete", "privacy"):
            candidate = (await self.prepare())["candidates"][0]
            if mode == "expiry":
                self.targets._records[candidate["candidate_id"]]["expires"] = time.monotonic() - 1
                self.perception.prune()
            elif mode == "delete":
                self.perception.delete(candidate["preview_id"])
            else:
                from unittest.mock import Mock
                core = SimpleNamespace(config={"carlos": {"privacy_mode": "PRIVATE SESSION"}},
                                       memory=Mock(), task_journal=Mock(), daily=Mock(), bus=Mock(),
                                       logger=Mock(), vision=self.perception)
                PrivacyPolicy(core).apply_storage()
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                await self.targets.review(candidate["candidate_id"])
            self.assertEqual(list(self.perception.capture_root.glob("*.png")), [])

    async def test_clear_during_inference_prevents_late_candidate_creation(self):
        async def cleared(*args):
            self.targets.clear()
            return await self.ocr(*args)
        self.perception.ocr = AsyncMock(side_effect=cleared)
        with self.assertRaisesRegex(ValueError, "cleared"):
            await self.prepare()
        self.assertEqual(list(self.perception.capture_root.glob("*.png")), [])
        self.assertFalse(self.targets._records)

    async def test_limit_bounded_history_and_no_external_mutation_of_identity(self):
        for _ in range(22):
            last = (await self.prepare())["candidates"][0]
        self.assertEqual(len(self.targets._records), 20)
        self.assertEqual(len(list(self.perception.capture_root.glob("*.png"))), 40)
        last["candidate_id"] = "fake"
        record = next(iter(self.targets._records.values()))
        record["target"]["window"]["geometry"]["x"] += 1
        self.assertEqual(self.window["geometry"]["x"], 10)

    async def test_invalid_or_stale_target_does_not_capture(self):
        for key, value in (("pid", True), ("deleted", True), ("minimized", True), ("normal", False)):
            original = deepcopy(self.window)
            self.window[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                await self.prepare()
            self.window.clear()
            self.window.update(original)
        self.desktop.snapshot = AsyncMock(return_value={**self.world, "captured_at_monotonic": 0})
        with self.assertRaises(ValueError):
            await self.prepare()
        self.perception.capture.assert_not_awaited()

    async def test_cloud_brain_and_invalid_confidence_refuse_before_capture(self):
        self.context.config["providers"]["active"] = "nvidia"
        spec = self.registry.get("vision.candidates")
        with self.assertRaisesRegex(ValueError, "cloud"):
            await spec.executor({"window_id": "exact", "text": "Retry"}, self.context)
        for score in (.5, float("nan"), True, 1.1):
            with self.subTest(score=score), self.assertRaises(ValueError):
                await self.targets.prepare("exact", "Retry", score)
        self.perception.capture.assert_not_awaited()

    async def test_click_has_mandatory_confirmation_and_checks_native_transition_once(self):
        arguments = await self.click_arguments()
        spec, validated = self.registry.validate("vision.candidate.click", arguments)
        self.assertTrue(spec.requires_confirmation)
        self.assertFalse(spec.public()["contract_gaps"])
        review = await self.targets.confirmation_review(arguments)
        self.assertTrue(review["preview_url"].startswith(self.perception.capture_root.as_uri()))
        self.assertIn("Retry", review["caption"])
        result = await self.registry.execute(spec, validated)
        self.assertTrue(result["verified"], result)
        self.assertFalse(result["before"]["verified"])
        self.assertTrue(result["after"]["verified"])
        self.assertEqual(self.input_events, [("NotifyPointerButton", (272, 1)),
                                           ("NotifyPointerButton", (272, 0))])
        self.assertTrue(evaluate_result(spec.name, result).verified)
        with self.assertRaises(ValueError):
            await self.targets.click(arguments, self.observe)
        self.assertEqual(len(self.input_events), 2)
        self.assertFalse(self.targets._records)
        self.assertEqual(list(self.perception.capture_root.glob("*.png")), [])

    async def test_changed_current_image_refuses_without_input_and_consumes_candidate(self):
        arguments = await self.click_arguments()
        original = self.capture

        async def changed(**kwargs):
            capture = await original(**kwargs)
            path = self.targets._path(capture["capture_id"])
            Image.new("RGB", (120, 80), "blue").save(path)
            return capture

        self.perception.capture = AsyncMock(side_effect=changed)
        with self.assertRaisesRegex(ValueError, "image changed"):
            await self.targets.click(arguments, self.observe)
        self.assertFalse(self.input_events)
        self.assertFalse(self.targets._records)
        self.assertEqual(list(self.perception.capture_root.glob("*.png")), [])

    async def test_png_metadata_change_does_not_hide_identical_pixels(self):
        from PIL.PngImagePlugin import PngInfo
        arguments = await self.click_arguments()
        original = self.capture

        async def metadata(**kwargs):
            capture = await original(**kwargs)
            path = self.targets._path(capture["capture_id"])
            info = PngInfo()
            info.add_text("fixture", "different PNG metadata")
            Image.new("RGB", (120, 80), "white").save(path, pnginfo=info)
            return capture

        self.perception.capture = AsyncMock(side_effect=metadata)
        self.assertTrue((await self.targets.click(arguments, self.observe))["verified"])

    async def test_unrelated_already_satisfied_and_unobservable_results_refuse_input(self):
        for mode in ("other_window", "already_true", "missing_state", "stale", "unsupported", "too_many"):
            arguments = await self.click_arguments()
            if mode == "other_window":
                arguments["expected"][0]["window_id"] = "elsewhere"
            elif mode == "already_true":
                arguments["expected"][0]["expected"] = False
            elif mode == "missing_state":
                del self.window["fullscreen"]
            elif mode == "unsupported":
                arguments["expected"] = [{"kind": "window_active", "window_id": "exact"}]
            elif mode == "too_many":
                arguments["expected"] *= 4
            observer = self.observe if mode != "stale" else AsyncMock(return_value={
                "status": "completed", "result": {**self.world, "captured_at_monotonic": 0}})
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                await self.targets.click(arguments, observer)
            self.window["fullscreen"] = False
            self.targets.clear()
        self.assertFalse(self.input_events)

    async def test_no_native_result_never_replays_delivered_click(self):
        arguments = await self.click_arguments()
        self.portal.notify = lambda method, *args: self.input_events.append((method, args))
        result = await self.targets.click(arguments, self.observe)
        self.assertTrue(result["input_sent"])
        self.assertFalse(result["verified"])
        self.assertFalse(result["replay_allowed"])
        receipt = evaluate_result("vision.candidate.click", result)
        self.assertEqual(receipt.status, "EXECUTED_UNVERIFIED")
        self.assertFalse(receipt.retryable)
        self.assertEqual(len(self.input_events), 2)
        with self.assertRaises(ValueError):
            await self.targets.click(arguments, self.observe)

    async def test_failed_delivery_remains_unknown_consumed_and_not_retryable(self):
        arguments = await self.click_arguments()
        self.desktop.input.click = AsyncMock(side_effect=RuntimeError("Fixture disconnected"))
        result = await self.targets.click(arguments, self.observe)
        self.assertTrue(result["delivery_unknown"])
        self.assertFalse(result["verified"])
        self.assertFalse(evaluate_result("vision.candidate.click", result).retryable)
        self.assertEqual(list(self.perception.capture_root.glob("*.png")), [])
        with self.assertRaises(ValueError):
            await self.targets.click(arguments, self.observe)
        self.desktop.input.click.assert_awaited_once()

    async def test_geometry_changes_during_pointer_move_block_button_press(self):
        arguments = await self.click_arguments()
        self.world["cursor"] = {"x": 30, "y": 30}
        original = self.notify

        def moved(method, *args):
            original(method, *args)
            if method == "NotifyPointerMotion":
                self.window["geometry"]["x"] += 1

        self.portal.notify = moved
        result = await self.targets.click(arguments, self.observe)
        self.assertFalse(result["verified"])
        self.assertEqual([event[0] for event in self.input_events], ["NotifyPointerMotion"])

    async def test_cancellation_after_press_releases_button_and_consumes_candidate(self):
        arguments = await self.click_arguments()
        entered, release = threading.Event(), threading.Event()
        original = self.notify

        def blocked(method, *args):
            original(method, *args)
            if method == "NotifyPointerButton" and args[-1] == 1:
                entered.set()
                release.wait(3)

        self.portal.notify = blocked
        task = asyncio.create_task(self.targets.click(arguments, self.observe))
        self.assertTrue(await asyncio.to_thread(entered.wait, 2))
        task.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.input_events[-1], ("NotifyPointerButton", (272, 0)))
        self.assertEqual(list(self.perception.capture_root.glob("*.png")), [])
        with self.assertRaises(ValueError):
            await self.targets.click(arguments, self.observe)

    async def test_thread_clear_during_preview_generation_prevents_partial_publication(self):
        thumbnail = Image.Image.thumbnail

        def revoke(image, *args, **kwargs):
            result = thumbnail(image, *args, **kwargs)
            thread = threading.Thread(target=self.targets.clear)
            thread.start()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertFalse(self.targets._records)
            return result

        with patch.object(Image.Image, "thumbnail", revoke), self.assertRaisesRegex(ValueError, "cleared"):
            await self.prepare()
        self.assertEqual(list(self.perception.capture_root.glob("*.png")), [])

    async def test_clear_during_fresh_capture_prevents_late_click(self):
        arguments = await self.click_arguments()
        original = self.capture

        async def revoked(**kwargs):
            self.targets.clear()
            return await original(**kwargs)

        self.perception.capture = AsyncMock(side_effect=revoked)
        with self.assertRaisesRegex(ValueError, "revoked"):
            await self.targets.click(arguments, self.observe)
        self.assertFalse(self.input_events)
        self.assertEqual(list(self.perception.capture_root.glob("*.png")), [])

    async def test_duplicate_labels_consumed_together_after_one_click(self):
        self.labels.append(label(60, 40))
        candidates = (await self.prepare())["candidates"]
        arguments = {"candidate_id": candidates[0]["candidate_id"], "expected": [
            {"kind": "window_state", "window_id": "exact", "property": "fullscreen", "expected": True}]}
        self.assertTrue((await self.targets.click(arguments, self.observe))["verified"])
        with self.assertRaises(ValueError):
            await self.targets.review(candidates[1]["candidate_id"])
        self.assertEqual(list(self.perception.capture_root.glob("*.png")), [])

    async def test_local_permission_preview_is_private_and_copied(self):
        arguments = await self.click_arguments()
        review = await self.targets.confirmation_review(arguments)
        broker = PermissionBroker()
        pending = broker.create("vision.candidate.click", arguments, Permission.HIGH,
                                review.pop("reason"), "fixture", review=review)
        review["preview_url"] = "changed"
        self.assertNotIn("review", pending.public(include_token=False))
        self.assertNotIn("review", broker.list_public()[0])
        client_review = broker.list_for_local_client()[0]["review"]
        self.assertNotEqual(client_review["preview_url"], "changed")
        client_review["preview_url"] = "changed again"
        self.assertNotEqual(pending.public()["review"]["preview_url"], "changed again")

    async def test_core_socket_confirmation_carries_preview_and_token_cannot_replay(self):
        from ev.paths import Paths
        from ev.service import CarlosCore
        from ev.desktop.world import DesktopWorldModel

        core = CarlosCore(paths=Paths(*(self.root / name for name in
                                      ("config", "data", "state", "cache", "runtime"))))
        core.config["providers"]["active"] = "offline"
        core.config["carlos"]["strict_permissions"] = False
        native = DesktopWorldModel(self.desktop.bridge)
        native.snapshot = self.desktop.snapshot
        native.input = self.desktop.input
        self.desktop = native
        self.perception.desktop = native
        core.vision, core.desktop = self.perception, self.desktop
        core.tools.context.vision, core.tools.context.desktop = self.perception, self.desktop
        await core.ipc.start()
        reader, writer = await asyncio.open_unix_connection(core.paths.socket)
        request_id = 0

        async def ipc(kind, payload):
            nonlocal request_id
            request_id += 1
            writer.write((json.dumps({"id": str(request_id), "type": kind, "payload": payload}) + "\n").encode())
            await writer.drain()
            while True:
                message = json.loads(await asyncio.wait_for(reader.readline(), 5))
                if message.get("id") == str(request_id):
                    return message

        try:
            arguments = await self.click_arguments()
            response = await ipc("tool.call", {"name": "vision.candidate.click", "arguments": arguments})
            pending = response["payload"]["confirmation"]
            self.assertEqual(response["payload"]["status"], "confirmation_required")
            self.assertIn("preview_url", pending["review"])
            self.assertFalse(self.input_events)
            self.assertNotIn("review", core.permissions.list_public()[0])
            decision = {"id": pending["id"], "approval_token": pending["approval_token"], "approved": True}
            response = await ipc("confirmation.respond", decision)
            self.assertEqual(response["payload"]["execution"]["status"], "SUCCEEDED_VERIFIED", response)
            self.assertEqual(len(self.input_events), 2)
            replay = await ipc("confirmation.respond", decision)
            self.assertEqual(replay["type"], "error")
            self.assertEqual(len(self.input_events), 2)
            self.window["fullscreen"] = False
            response = await ipc("command.submit", {"text": 'visually click "Retry" expecting window to be fullscreen',
                                                     "speak": False})
            self.assertEqual(response["payload"]["status"], "confirmation_required", response)
            pending = response["payload"]["confirmation"]
            self.assertEqual(pending["tool"], "vision.click_text")
            self.assertIn("preview_url", pending["review"])
            self.assertIn("candidate_id", pending["arguments"])
            self.assertEqual(len(self.input_events), 2)
            decision = {"id": pending["id"], "approval_token": pending["approval_token"], "approved": True}
            response = await ipc("confirmation.respond", decision)
            self.assertTrue(response["payload"]["result"]["verified"], response)
            self.assertEqual(len(self.input_events), 4)
        finally:
            writer.close()
            await writer.wait_closed()
            await core.ipc.stop()
            core.daily.close()
            core.task_journal.close()
            core.memory.close()

    async def test_text_confirmation_refuses_duplicate_labels_without_explicit_ordinal(self):
        self.labels = [label(60, 40), label(10, 10)]
        arguments = {"window_id": "exact", "text": "Retry", "expected": [
            {"kind": "window_state", "window_id": "exact", "property": "fullscreen", "expected": True}]}
        with self.assertRaisesRegex(ValueError, "Multiple"):
            await self.targets.prepare_text_confirmation(arguments)
        self.assertFalse(self.input_events)
        self.targets.clear()
        bound, review = await self.targets.prepare_text_confirmation({**arguments, "ordinal": 2,
                                                                     "candidate_id": "0" * 32})
        self.assertNotEqual(bound["candidate_id"], "0" * 32)
        record = self.targets._records[bound["candidate_id"]]
        self.assertEqual(record["element"]["center"], {"x": 80, "y": 47.5})
        self.assertIn("Retry", review["caption"])
        self.assertFalse(self.input_events)

    async def test_text_confirmation_invalid_ordinal_and_different_target_refuse(self):
        arguments = {"window_id": "exact", "text": "Retry", "expected": [
            {"kind": "window_state", "window_id": "exact", "property": "fullscreen", "expected": True}]}
        for ordinal in (0, 21, True, "2"):
            with self.subTest(ordinal=ordinal), self.assertRaises(ValueError):
                await self.targets.prepare_text_confirmation({**arguments, "ordinal": ordinal})
        self.perception.capture.assert_not_awaited()
        with self.assertRaisesRegex(ValueError, "different window"):
            await self.targets.prepare_text_confirmation({**arguments, "window_id": "elsewhere"})
        self.perception.capture.assert_not_awaited()
        with self.assertRaisesRegex(ValueError, "ordinal does not exist"):
            await self.targets.prepare_text_confirmation({**arguments, "ordinal": 2})
        self.assertFalse(self.input_events)

    async def test_text_executor_connects_native_input_only_when_executing_bound_confirmation(self):
        spec = self.registry.get("vision.click_text")
        arguments = {"window_id": "exact", "text": "Retry", "expected": [
            {"kind": "window_state", "window_id": "exact", "property": "fullscreen", "expected": True}]}
        bound, review = await self.targets.prepare_text_confirmation(arguments)
        self.portal.status = lambda: {"connected": False}

        async def connect():
            self.assertFalse(self.input_events)
            self.portal.status = lambda: {"connected": True}
            return {"connected": True, "verified": True}

        self.desktop.input.connect = AsyncMock(side_effect=connect)
        with self.assertRaisesRegex(ValueError, "preview confirmation"):
            await self.registry.execute(spec, arguments)
        self.desktop.input.connect.assert_not_awaited()
        result = await self.registry.execute(spec, bound)
        self.assertTrue(result["verified"])
        self.desktop.input.connect.assert_awaited_once()
        self.assertEqual(len(self.input_events), 2)
