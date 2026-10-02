import asyncio
import hashlib
import json
import logging
import os
import tempfile
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
                      "current_desktop": "desk", "active_window_id": "exact"}
        self.desktop = SimpleNamespace(snapshot=AsyncMock(side_effect=self.snapshot))
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
