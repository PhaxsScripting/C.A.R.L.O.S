import math
import time


def recent_age(observed_at, max_age, *, now=None):
    if type(observed_at) not in (int, float):
        return None
    current = time.monotonic() if now is None else now
    if type(current) not in (int, float):
        return None
    try:
        if not math.isfinite(observed_at) or not math.isfinite(current):
            return None
        age = current - observed_at
    except OverflowError:
        return None
    return age if 0 <= age <= max_age else None
