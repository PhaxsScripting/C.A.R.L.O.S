"""Image-local OCR geometry and bounded private PNG reads."""

import hashlib
import io
import math
import os
import stat
from pathlib import Path

from PIL import Image


def private_png(path: Path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) & 0o077
                or not 0 < before.st_size <= 16 * 1024 * 1024):
            raise ValueError("Capture must be a private owned PNG of at most 16 MiB")
        data = source.read(16 * 1024 * 1024 + 1)
        after = os.fstat(source.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("Capture changed while reading")
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        if (image.format != "PNG" or not 0 < width * height <= 16_000_000
                or getattr(image, "n_frames", 1) != 1):
            raise ValueError("Capture must be one bounded PNG frame")
        image.verify()
    return data, (width, height), hashlib.sha256(data).hexdigest()


def validate_element(element, width, height, minimum_score):
    if not isinstance(element, dict):
        raise ValueError("OCR element is not an object")
    score, text = element.get("confidence"), element.get("text")
    if (type(score) not in (int, float) or not math.isfinite(score)
            or not minimum_score <= score <= 1 or not isinstance(text, str)
            or not text.strip() or len(text) > 4096):
        raise ValueError("OCR confidence or text is invalid")
    text.encode("utf-8")
    box, center = element.get("box"), element.get("center")
    if (not isinstance(box, list) or len(box) != 4
            or not isinstance(center, dict) or set(center) != {"x", "y"}):
        raise ValueError("OCR geometry needs four corners and a center")
    for point in box:
        if (not isinstance(point, list) or len(point) != 2
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       for value in point)
                or not 0 <= point[0] <= width or not 0 <= point[1] <= height):
            raise ValueError("OCR box is outside its capture")
    turns = []
    for i in range(4):
        a, b, c = box[i], box[(i + 1) % 4], box[(i + 2) % 4]
        turns.append((b[0] - a[0]) * (c[1] - b[1])
                     - (b[1] - a[1]) * (c[0] - b[0]))
    area = abs(sum(box[i][0] * box[(i + 1) % 4][1]
                   - box[(i + 1) % 4][0] * box[i][1] for i in range(4))) / 2
    if not (all(turn > 0 for turn in turns) or all(turn < 0 for turn in turns)) or area < 1:
        raise ValueError("OCR box is degenerate or crosses itself")
    for axis, limit, coordinate in (("x", width, 0), ("y", height, 1)):
        value = center[axis]
        if (type(value) not in (int, float) or not math.isfinite(value)
                or not 0 <= value < limit
                or abs(value - sum(p[coordinate] for p in box) / 4) > .11):
            raise ValueError("OCR center does not match its box")
    return {"text": text, "confidence": float(score), "box": box, "center": center}
