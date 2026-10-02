"""Readiness uses current observations instead of cached success labels."""
import math
from datetime import datetime
import time


def project_readiness(snapshot, capabilities, startup_seconds=None, fresh_after=None):
    def mapping(value):
        return value if isinstance(value, dict) else {}

    voice = mapping(snapshot.get("voice"))
    capabilities = mapping(capabilities)
    rows = mapping(capabilities.get("capabilities", capabilities))
    now = time.time()

    def observed(row, max_age):
        row = mapping(row)
        state = row.get("state", "UNVERIFIED")
        if not isinstance(state, str):
            return "UNVERIFIED"
        if state != "READY":
            return state
        stamp = row.get("observed_at", row.get("checked_at"))
        if not isinstance(stamp, (float, int)) or isinstance(stamp, bool) or not math.isfinite(stamp):
            return "UNVERIFIED"
        if fresh_after is not None and stamp < fresh_after:
            return "STALE"
        return "READY" if 0 <= now - stamp <= max_age else "STALE"

    health = mapping(snapshot.get("health"))
    model = health.get("Local AI", rows.get("LocalAI", {}))
    workers = mapping(voice.get("workers"))
    speech = {}
    for name in ("VAD", "STT", "TTS"):
        worker = mapping(workers.get(name))
        mode = worker.get("mode")
        if mode == "DISABLED" and name == "VAD":
            speech[name] = "DISABLED"
        elif mode == "ON_DEMAND" and name != "VAD":
            speech[name] = "ON_DEMAND" if worker.get("available") is True else "UNAVAILABLE"
        elif mode == "PERSISTENT":
            state = observed(health.get(name, {}), 60)
            speech[name] = state if state != "READY" or worker.get("running") is True else "STOPPED"
        else:
            speech[name] = "UNVERIFIED"
    speech_ready = all(speech[name] == "READY" if mapping(workers.get(name)).get("mode") == "PERSISTENT"
                       else speech[name] in {"ON_DEMAND", "DISABLED"} for name in speech)
    components = {
        "Core": "READY",
        "Voice": "MUTED" if voice.get("privacy_mode") else
                 "DISABLED" if not voice.get("wake_enabled") else
                 "PAUSED" if voice.get("wake_paused") else
                 "SUSPENDED" if voice.get("resource_suspended") else
                 "STARTING" if not (voice.get("wake_active") is True and voice.get("stt_available") is True and voice.get("tts_available") is True) else
                 "READY" if speech_ready else "DEGRADED",
        "Local AI": observed(model, 60),
        "Desktop": "READY" if snapshot.get("desktop", {}).get("available") else "UNVERIFIED",
        "Remote": observed(rows.get("Remote", {}), 15),
        **speech,
    }
    return {
        "state": "FULLY_READY" if all(components[name] == "READY" for name in ("Core", "Voice", "Local AI", "Desktop", "Remote")) else "PARTIAL",
        "components": components,
        "observed_at": now,
        "startup_to_ipc_seconds": startup_seconds,
        "boot_to_usable_seconds": None,
        "resume_to_usable_seconds": None,
        "scope": "Process startup and current component observations; OS boot and resume require separate measurements",
    }


class ReadinessTiming:
    def __init__(self, started):
        self.started = started
        self.startup_components = {}
        self.startup_ready = None
        self.resume = None

    def resumed(self, event):
        stamp = event.payload.get("observed_monotonic")
        interval = event.payload.get("detection_interval_seconds")
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                   and math.isfinite(x) and x >= 0 for x in (stamp, interval)):
            return
        if self.resume is not None and stamp <= self.resume["monotonic"]:
            return
        try:
            observed_at = datetime.fromisoformat(event.timestamp).timestamp()
        except (TypeError, ValueError):
            return
        self.resume = {"monotonic": stamp, "observed_at": observed_at,
                       "detection_interval_seconds": interval,
                       "components_seconds": {}, "detection_to_all_ready_seconds": None,
                       "resume_to_all_ready_range_seconds": None}

    def observe(self, readiness, now=None):
        now = time.monotonic() if now is None else now
        components = readiness["components"]
        for name, state in components.items():
            if state != "READY":
                continue
            self.startup_components.setdefault(name, round(max(0, now - self.started), 3))
            if self.resume is not None and now >= self.resume["monotonic"]:
                self.resume["components_seconds"].setdefault(
                    name, round(now - self.resume["monotonic"], 3))
        if readiness["state"] == "FULLY_READY":
            if self.startup_ready is None:
                self.startup_ready = round(max(0, now - self.started), 3)
            if (self.resume is not None and now >= self.resume["monotonic"]
                    and self.resume["detection_to_all_ready_seconds"] is None):
                elapsed = round(max(0, now - self.resume["monotonic"]), 3)
                self.resume["detection_to_all_ready_seconds"] = elapsed
                self.resume["resume_to_all_ready_range_seconds"] = [
                    elapsed, round(elapsed + self.resume["detection_interval_seconds"], 3)]

    def snapshot(self):
        return {"startup_components_seconds": dict(self.startup_components),
                "startup_to_all_ready_seconds": self.startup_ready,
                "last_resume": None if self.resume is None else {
                    key: (dict(value) if isinstance(value, dict) else list(value) if isinstance(value, list) else value)
                    for key, value in self.resume.items() if key != "monotonic"},
                "scope": "First observed component readiness; resume includes detection uncertainty. No OS boot benchmark."}
