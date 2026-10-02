"""Short-lived, window-bound visual proposals. These do not send input."""

import asyncio
import io
import math
import os
import time
import uuid
from copy import deepcopy

from PIL import Image, ImageDraw

from .vision_geometry import private_png, validate_element


class VisualTargets:
    def __init__(self, perception):
        self.perception = perception
        self._records = {}
        self._lock = asyncio.Lock()
        self._generation = 0

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
                "on_all_desktops", "fullscreen", "maximize_mode")},
            "process": {k: lifetime[k] for k in ("pid", "boot_id", "start_ticks")},
            "output": {k: output.get(k) for k in (
                "name", "geometry", "scale", "transform", "manufacturer", "model", "serial")},
            "current_desktop": world.get("current_desktop"),
        })

    def _drop(self, identifiers):
        removed = [self._records.pop(key) for key in identifiers if key in self._records]
        retained_sources = {record["capture_id"] for record in self._records.values()}
        for record in removed:
            self._path(record["preview_id"]).unlink(missing_ok=True)
            if record["capture_id"] not in retained_sources:
                self._path(record["capture_id"]).unlink(missing_ok=True)

    def _path(self, capture_id):
        return self.perception.capture_root / (capture_id + ".png")

    def prune(self):
        now = time.monotonic()
        self._drop([key for key, record in self._records.items() if record["expires"] <= now])

    def clear(self):
        self._generation += 1
        self._drop(list(self._records))

    def forget_capture(self, capture_id):
        self._drop([key for key, record in self._records.items()
                    if capture_id in (record["capture_id"], record["preview_id"])])

    async def prepare(self, window_id, text, minimum_score=.8):
        if (not isinstance(window_id, str) or not 1 <= len(window_id) <= 100
                or not isinstance(text, str) or not text.strip() or len(text) > 500
                or type(minimum_score) not in (int, float) or not math.isfinite(minimum_score)
                or not .8 <= minimum_score <= 1):
            raise ValueError("Visual proposal needs an exact window and text, with confidence at least 0.8")
        if self._lock.locked():
            raise RuntimeError("A visual proposal is already being prepared")
        generation = self._generation
        async with self._lock:
            self.prune()
            before = await self._target(window_id)
            capture = await self.perception.capture(window_id=window_id)
            source_id = capture["capture_id"]
            staged, committed = [], False
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
                    self._records[identifier] = {
                        "candidate_id": identifier, "capture_id": source_id, "preview_id": preview_id,
                        "preview_sha256": private_png(preview)[2], "capture_sha256": digest,
                        "target": before, "element": element, "expires": expires,
                    }
                if generation != self._generation:
                    raise ValueError("Visual proposals were cleared while preparing this request")
                while len(self._records) > 20:
                    self._drop([next(iter(self._records))])
                committed = True
                return {
                    "candidates": [self._public(self._records[key]) for key, _ in staged],
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
        record = self._records.get(candidate_id)
        if record is None:
            raise ValueError("Visual candidate is missing or expired; inspect again")
        record = deepcopy(record)
        try:
            if (await self._target(record["target"]["window"]["id"]) != record["target"]
                    or private_png(self._path(record["capture_id"]))[2] != record["capture_sha256"]
                    or private_png(self._path(record["preview_id"]))[2] != record["preview_sha256"]
                    or self._records.get(candidate_id) != record or record["expires"] <= time.monotonic()):
                raise ValueError("Visual candidate changed; inspect again")
        except BaseException:
            self._drop([candidate_id])
            raise
        return self._public(record)
