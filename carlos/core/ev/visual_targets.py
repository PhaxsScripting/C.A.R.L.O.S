"""Short-lived, window-bound visual proposals. These do not send input."""

import asyncio
import hashlib
import io
import json
import math
import os
import time
import threading
import uuid
from copy import deepcopy

from PIL import Image, ImageDraw

from .vision_geometry import private_png, validate_element
from .goals import validate_conditions, verify_conditions


class VisualTargets:
    def __init__(self, perception):
        self.perception = perception
        self._records = {}
        self._lock = asyncio.Lock()
        self._generation = 0
        self._state_lock = threading.RLock()

    async def _target(self, window_id):
        world = await self.perception.desktop.snapshot(force=True)
        captured = world.get("captured_at_monotonic")
        if type(captured) not in (int, float) or not 0 <= time.monotonic() - captured <= 2:
            raise ValueError("Visual target needs a fresh native window observation")
        matches = [w for w in world.get("windows", []) if w.get("id") == window_id]
        if (len(matches) != 1 or matches[0].get("deleted") or matches[0].get("minimized")
                or matches[0].get("special")
                or not (matches[0].get("normal") or matches[0].get("dialog"))):
            raise ValueError("Exact visual target window is unavailable")
        window = matches[0]
        pid, geometry = window.get("pid"), window.get("geometry")
        if (type(pid) is not int or pid <= 0 or not isinstance(geometry, dict)
                or set(geometry) != {"x", "y", "width", "height"}
                or any(type(v) not in (int, float) or not math.isfinite(v)
                       or not -32768 <= v <= 32768 for v in geometry.values())
                or geometry["width"] <= 0 or geometry["height"] <= 0):
            raise ValueError("Visual target has incomplete native identity or geometry")
        from .tools.process_lifetime import process_lifetime

        lifetime = process_lifetime({"pid": pid}, None)
        if lifetime["lifetime_status"] != "RUNNING":
            raise ValueError("Visual target process is not running")
        outputs = [o for o in world.get("outputs", [])
                   if o.get("name") == window.get("output") and o.get("enabled", True)]
        if len(outputs) != 1:
            raise ValueError("Visual target needs an exact enabled output")
        output = outputs[0]
        return deepcopy({
            "window": {k: window.get(k) for k in (
                "id", "pid", "title", "app_id", "geometry", "output", "desktops",
                "on_all_desktops", "fullscreen", "maximize_mode", "client_geometry")},
            "process": {k: lifetime[k] for k in ("pid", "boot_id", "start_ticks")},
            "output": {k: output.get(k) for k in (
                "name", "geometry", "scale", "transform", "rotation", "manufacturer", "model",
                "serial", "serial_number")},
            "current_desktop": world.get("current_desktop"),
        })

    def _drop(self, identifiers):
        with self._state_lock:
            self._drop_locked(identifiers)

    def _drop_locked(self, identifiers):
        removed = [self._records.pop(key) for key in identifiers if key in self._records]
        retained_sources = {record["capture_id"] for record in self._records.values()}
        for record in removed:
            self._path(record["preview_id"]).unlink(missing_ok=True)
            if record["capture_id"] not in retained_sources:
                self._path(record["capture_id"]).unlink(missing_ok=True)

    def _path(self, capture_id):
        return self.perception.capture_root / (capture_id + ".png")

    @staticmethod
    def _pixels(data):
        with Image.open(io.BytesIO(data)) as image:
            return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()

    def prune(self):
        with self._state_lock:
            now = time.monotonic()
            self._drop_locked([key for key, record in self._records.items() if record["expires"] <= now])

    def clear(self):
        with self._state_lock:
            self._generation += 1
            self._drop_locked(list(self._records))

    def forget_capture(self, capture_id):
        with self._state_lock:
            self._drop_locked([key for key, record in self._records.items()
                              if capture_id in (record["capture_id"], record["preview_id"])])

    async def prepare(self, window_id, text, minimum_score=.8):
        if (not isinstance(window_id, str) or not 1 <= len(window_id) <= 100
                or not isinstance(text, str) or not text.strip() or len(text) > 500
                or type(minimum_score) not in (int, float) or not math.isfinite(minimum_score)
                or not .8 <= minimum_score <= 1):
            raise ValueError("Visual proposal needs an exact window and text, with confidence at least 0.8")
        if self._lock.locked():
            raise RuntimeError("A visual proposal is already being prepared")
        with self._state_lock:
            generation = self._generation
        async with self._lock:
            self.prune()
            before = await self._target(window_id)
            capture = await self.perception.capture(window_id=window_id)
            source_id = capture["capture_id"]
            staged, records, committed = [], {}, False
            try:
                data, (width, height), digest = private_png(self._path(source_id))
                geometry = before["window"]["geometry"]
                if ((width, height) != (geometry["width"], geometry["height"])
                        or capture.get("kind") != "window" or capture.get("sha256") != digest):
                    raise ValueError("Capture does not map exactly to the native target")
                observed = await self.perception.ocr(source_id, minimum_score)
                if observed.get("capture_sha256") != digest or await self._target(window_id) != before:
                    raise ValueError("Window or capture changed while locating visual candidates")
                needle = " ".join(text.split()).casefold()
                matches = [validate_element(e, width, height, minimum_score)
                           for e in observed.get("elements", [])
                           if " ".join(e["text"].split()).casefold() == needle]
                if len(matches) > 20:
                    raise ValueError("Too many matching labels; use structured controls or a narrower target")
                expires = time.monotonic() + 60
                for element in matches:
                    identifier, preview_id = uuid.uuid4().hex, uuid.uuid4().hex
                    preview = self._path(preview_id)
                    staged.append((identifier, preview_id))
                    with Image.open(io.BytesIO(data)) as image:
                        image = image.convert("RGB")
                        draw = ImageDraw.Draw(image)
                        draw.line([tuple(p) for p in element["box"]] + [tuple(element["box"][0])],
                                  fill="#ffb947", width=4)
                        x, y = element["center"]["x"], element["center"]["y"]
                        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill="#ffb947")
                        image.thumbnail((1280, 720))
                        descriptor = os.open(preview, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(descriptor, "wb") as output:
                            image.save(output, format="PNG")
                    records[identifier] = {
                        "candidate_id": identifier, "capture_id": source_id, "preview_id": preview_id,
                        "preview_sha256": private_png(preview)[2], "capture_sha256": digest,
                        "capture_pixels_sha256": self._pixels(data),
                        "target": before, "element": element, "expires": expires,
                    }
                with self._state_lock:
                    if generation != self._generation:
                        raise ValueError("Visual proposals were cleared while preparing this request")
                    self._records.update(records)
                    while len(self._records) > 20:
                        self._drop_locked([next(iter(self._records))])
                    public = [self._public(self._records[key]) for key, _ in staged]
                    committed = True
                return {
                    "candidates": public,
                    "ambiguous": len(matches) > 1, "matched": len(matches),
                    "coordinate_actions_allowed": False, "scene_accuracy_verified": False,
                    "local_only": True, "uploaded": False,
                    "message": "Review the highlighted label. This historical image does not authorize input.",
                }
            finally:
                if not committed or not staged:
                    self._drop([key for key, _ in staged])
                    for _, preview_id in staged:
                        self._path(preview_id).unlink(missing_ok=True)
                    self._path(source_id).unlink(missing_ok=True)

    def _public(self, record):
        return deepcopy({
            "candidate_id": record["candidate_id"], "capture_id": record["capture_id"],
            "preview_id": record["preview_id"], "preview_path": str(self._path(record["preview_id"])),
            "text": record["element"]["text"], "confidence": record["element"]["confidence"],
            "window_id": record["target"]["window"]["id"],
            "window_title": record["target"]["window"]["title"],
            "expires_in_seconds": max(0, int(record["expires"] - time.monotonic())),
        })

    async def review(self, candidate_id):
        self.prune()
        with self._state_lock:
            record = deepcopy(self._records.get(candidate_id))
            if record is None:
                raise ValueError("Visual candidate is missing or expired; inspect again")
        try:
            if (await self._target(record["target"]["window"]["id"]) != record["target"]
                    or private_png(self._path(record["capture_id"]))[2] != record["capture_sha256"]
                    or private_png(self._path(record["preview_id"]))[2] != record["preview_sha256"]
                    or not self._current(record)):
                raise ValueError("Visual candidate changed; inspect again")
        except BaseException:
            self._drop([candidate_id])
            raise
        return self._public(record)

    def _current(self, record):
        with self._state_lock:
            return (self._records.get(record["candidate_id"]) == record
                    and record["expires"] > time.monotonic())

    @staticmethod
    def validate_click(arguments):
        conditions = arguments["expected"]
        validate_conditions(conditions)
        if len(conditions) > 3:
            raise ValueError("Visual clicks support at most three native result conditions")
        for condition in conditions:
            if condition["kind"] not in {
                "window_absent", "window_state", "window_geometry", "control_state",
                "control_text", "browser_url",
            }:
                raise ValueError("Visual clicks need an observable change in their exact target window")
        return arguments

    def _validate_expected_target(self, record, conditions):
        window = record["target"]["window"]
        for condition in conditions:
            target = condition.get("target", condition)
            if target.get("window_id") != window["id"]:
                raise ValueError("Expected result belongs to a different window")
            if condition["kind"].startswith("control_") and (
                target["process_id"] != window["pid"] or target["window_title"] != window["title"]
            ):
                raise ValueError("Expected control belongs to a different process or title")

    async def confirmation_review(self, arguments):
        self.validate_click(arguments)
        public = await self.review(arguments["candidate_id"])
        with self._state_lock:
            record = deepcopy(self._records.get(arguments["candidate_id"]))
            if record is None:
                raise ValueError("Visual candidate expired during review")
            self._validate_expected_target(record, arguments["expected"])
        return {
            "preview_url": self._path(record["preview_id"]).as_uri(),
            "caption": f'{public["text"]} in {public["window_title"] or public["window_id"]} '
                       f'({public["confidence"]:.0%} OCR confidence)',
            "reason": "Send one left click at the highlighted label. Carlos will capture this window again "
                      "and refuse if the image or target changed. Expected native result: "
                      + json.dumps(arguments["expected"], ensure_ascii=False),
        }

    async def click(self, arguments, requester):
        if self._lock.locked():
            raise RuntimeError("Another visual operation is already running")
        async with self._lock:
            return await self._click(arguments, requester)

    async def _click(self, arguments, requester):
        self.validate_click(arguments)
        candidate_id = arguments["candidate_id"]
        await self.review(candidate_id)
        with self._state_lock:
            record = self._records.get(candidate_id)
            if record is None:
                raise ValueError("Visual candidate was already consumed")
            self._validate_expected_target(record, arguments["expected"])
            record = deepcopy(self._records.pop(candidate_id))
            for key, sibling in tuple(self._records.items()):
                if sibling["capture_id"] == record["capture_id"]:
                    self._records.pop(key)
                    self._path(sibling["preview_id"]).unlink(missing_ok=True)
            generation = self._generation
        window_id = record["target"]["window"]["id"]
        fresh_id = None
        dispatched = False

        async def guard():
            with self._state_lock:
                if generation != self._generation or record["expires"] <= time.monotonic():
                    raise ValueError("Visual candidate was revoked or expired")
            if (await self._target(window_id) != record["target"]
                    or private_png(self._path(record["capture_id"]))[2] != record["capture_sha256"]
                    or private_png(self._path(record["preview_id"]))[2] != record["preview_sha256"]):
                raise ValueError("Visual candidate changed; no click was sent")
            with self._state_lock:
                if generation != self._generation:
                    raise ValueError("Visual candidate was revoked")
            return deepcopy(record["target"]["window"])

        try:
            await guard()
            await self.perception.desktop.bridge.request("activate", {"window_id": window_id}, timeout=4)
            await guard()
            before = await verify_conditions(arguments["expected"], requester, candidate_id)
            if any(r.get("error") or r["verified"] or r.get("actual") is None
                   or r.get("actual") == {} for r in before["conditions"]):
                raise ValueError("Expected native change is already satisfied or cannot be observed; no click was sent")
            capture = await self.perception.capture(window_id=window_id)
            fresh_id = capture["capture_id"]
            data, dimensions, _ = private_png(self._path(fresh_id))
            geometry = record["target"]["window"]["geometry"]
            if (capture.get("kind") != "window"
                    or dimensions != (geometry["width"], geometry["height"])
                    or self._pixels(data) != record["capture_pixels_sha256"]):
                raise ValueError("Window image changed after review; inspect again")
            await guard()
            geometry, center = record["target"]["window"]["geometry"], record["element"]["center"]
            dispatched = True
            delivery = await self.perception.desktop.input.click(
                window_id, geometry["x"] + center["x"], geometry["y"] + center["y"],
                before_press=guard,
            )
            if delivery.get("input_sent") is not True:
                raise RuntimeError("Desktop input did not acknowledge delivery")
            after = None
            for attempt in range(6):
                after = await verify_conditions(arguments["expected"], requester, candidate_id)
                if after["verified"]:
                    break
                if attempt < 5:
                    await asyncio.sleep(.3)
            return {
                "candidate_id": candidate_id, "window_id": window_id,
                "input_sent": True, "verified": after["verified"], "replay_allowed": False,
                "verification_scope": "declared_native_transition", "before": before, "after": after,
                "message": ("Declared native change observed after one click." if after["verified"] else
                            "One click was sent, but the declared result was not observed. It will not be replayed."),
            }
        except Exception as error:
            if not dispatched:
                raise
            return {
                "candidate_id": candidate_id, "window_id": window_id,
                "input_sent": False, "delivery_unknown": True, "verified": False,
                "replay_allowed": False, "verification_scope": "declared_native_transition",
                "message": f"Input attempt could not be confirmed: {error}. Inspect before trying a new candidate.",
            }
        finally:
            if fresh_id:
                self.perception.delete(fresh_id)
            with self._state_lock:
                self._drop_locked([key for key, item in self._records.items()
                                   if item["capture_id"] == record["capture_id"]])
                self._path(record["capture_id"]).unlink(missing_ok=True)
                self._path(record["preview_id"]).unlink(missing_ok=True)
