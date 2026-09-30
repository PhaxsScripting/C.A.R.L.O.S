"""Readiness uses current observations instead of cached success labels."""
import math
import time


def project_readiness(snapshot, capabilities, startup_seconds=None):
    voice = snapshot.get("voice", {})
    rows = capabilities.get("capabilities", capabilities)
    now = time.time()

    def observed(row, max_age):
        state = row.get("state", "UNVERIFIED")
        if state != "READY":
            return state
        stamp = row.get("observed_at", row.get("checked_at"))
        if not isinstance(stamp, (float, int)) or isinstance(stamp, bool) or not math.isfinite(stamp):
            return "UNVERIFIED"
        return "READY" if 0 <= now - stamp <= max_age else "STALE"

    health = snapshot.get("health", {})
    model = health.get("Local AI", rows.get("LocalAI", {}))
    components = {
        "Core": "READY",
        "Voice": "MUTED" if voice.get("privacy_mode") else
                 "DISABLED" if not voice.get("wake_enabled") else
                 "PAUSED" if voice.get("wake_paused") else
                 "SUSPENDED" if voice.get("resource_suspended") else
                 "READY" if voice.get("wake_active") and voice.get("stt_available") and voice.get("tts_available") else "STARTING",
        "Local AI": observed(model, 60),
        "Desktop": "READY" if snapshot.get("desktop", {}).get("available") else "UNVERIFIED",
        "Remote": observed(rows.get("Remote", {}), 15),
    }
    return {
        "state": "FULLY_READY" if all(value == "READY" for value in components.values()) else "PARTIAL",
        "components": components,
        "observed_at": now,
        "startup_to_ipc_seconds": startup_seconds,
        "boot_to_usable_seconds": None,
        "resume_to_usable_seconds": None,
        "scope": "Process startup and current component observations; OS boot and resume require separate measurements",
    }
