"""Session presence evidence, without camera frames or claims of identity."""

import asyncio
import time


class PresenceMonitor:
    def __init__(self, bus, config=None):
        self.bus = bus
        self.config = config if config is not None else {}
        self.state = {"session": "UNKNOWN", "person_identity": "UNVERIFIED", "camera_used": False}
        self.previous_lock = None
        self.sleep_offset = None
        self.last_clock_check = None
        self.last_addressed = 0.0
        self.locked_since = None
        self.last_greeting = None
        self.idle_supported = None
        self.state.update(presence="UNKNOWN", attention="DORMANT", confidence=0.0)

    def consume(self, event):
        now = time.monotonic()
        if event.type in {"wake.detected", "command.received", "voice.transcription_complete"}:
            self.last_addressed = now
            self.state.update(
                attention="ENGAGED",
                presence="ENGAGED",
                confidence=0.9,
                evidence="Explicit assistant interaction; person identity unverified",
            )
        elif event.type == "voice.listening_started":
            self.state["attention"] = "WAITING_FOR_USER"
        elif event.type in {"voice.conversation_ended", "carlos.privacy_changed"}:
            self.last_addressed = 0.0
            self.state.update(
                attention="DORMANT",
                presence="UNKNOWN",
                confidence=0.0,
                evidence="Previous interaction context cleared",
            )
        elif event.type == "voice.barge_in":
            self.state["attention"] = "INTERRUPTED"

    def observe_lock(self, locked, now=None):
        now = time.monotonic() if now is None else now
        self.state.update(
            session="LOCKED" if locked else "UNLOCKED", observed_at=time.time(), camera_used=False
        )
        if locked:
            if self.locked_since is None:
                self.locked_since = now
            self.state.update(
                presence="LIKELY_ABSENT",
                confidence=0.55,
                evidence="Screen locked; physical absence is not proven",
                attention="DORMANT",
            )
        else:
            away = now - self.locked_since if self.locked_since is not None else 0
            if (
                self.config.get("greetings", True)
                and self.previous_lock is True
                and away >= self.config.get("away_seconds", 300)
                and (self.last_greeting is None or now - self.last_greeting >= self.config.get("cooldown_seconds", 1800))
            ):
                self.bus.publish(
                    "presence.returned",
                    "presence",
                    {
                        "away_seconds": round(away),
                        "greeting": "Welcome back.",
                        "delivery": "HUD_ONLY",
                        "identity": "UNVERIFIED",
                    },
                )
                self.last_greeting = now
            self.locked_since = None
            engaged = bool(self.last_addressed and now - self.last_addressed < 30)
            self.state.update(
                presence="ENGAGED" if engaged else "UNKNOWN",
                confidence=0.9 if engaged else 0.2,
                evidence=(
                    "Recent explicit interaction"
                    if engaged
                    else "Unlocked session alone does not prove desk occupancy"
                ),
            )
            if not engaged:
                self.state["attention"] = "DORMANT"
        if locked != self.previous_lock:
            self.bus.publish("presence.session_changed", "presence", dict(self.state))
        self.previous_lock = locked

    def observe_hand(self, status, now=None):
        """Use only fresh metadata from the existing tracker; never open a camera."""
        now = time.monotonic() if now is None else now
        if not self.config.get("hand_presence", True) or self.state.get("session") != "UNLOCKED":
            return
        if self.last_addressed and now - self.last_addressed < 30:
            return
        tracking = status.get("tracking", {})
        if (
            status.get("state") == "READY"
            and tracking.get("hand_visible") is True
            and isinstance(tracking.get("confidence"), (int, float))
            and tracking["confidence"] >= 0.8
            and isinstance(tracking.get("age_ms"), (int, float))
            and 0 <= tracking["age_ms"] <= 1000
        ):
            idle = self.state.get("idle_seconds")
            active = isinstance(idle, (int, float)) and 0 <= idle <= 60
            self.state.update(
                presence="AT_DESK" if active else "PRESENT",
                confidence=0.75 if active else 0.65,
                camera_used=True,
                evidence="Fresh hand metadata from the already-running HoloHand tracker; identity unverified",
            )
        else:
            self.state.update(
                presence="UNKNOWN",
                confidence=0.2,
                camera_used=False,
                evidence="No fresh desk evidence; absence is not proven",
            )

    def observe_clock(self, boottime, monotonic):
        offset = boottime - monotonic
        if self.sleep_offset is not None and offset - self.sleep_offset > 2:
            self.bus.publish("system.resume_observed", "presence", {
                "suspended_seconds": round(offset - self.sleep_offset, 2),
                "observed_monotonic": monotonic,
                "detection_interval_seconds": round(max(0, monotonic - self.last_clock_check), 3),
            })
        self.sleep_offset = offset
        self.last_clock_check = monotonic

    def observe_session(self, locked, idle, backend):
        if locked is None:
            changed = self.state.get("session") != "UNKNOWN"
            self.previous_lock = self.locked_since = None
            self.last_addressed = 0.0
            self.idle_supported = None
            self.state.update(session="UNKNOWN", presence="UNKNOWN", confidence=0.0,
                              camera_used=False, idle_seconds=None, idle_supported=False,
                              attention="DORMANT", observed_at=time.time(),
                              lock_backend=backend, evidence="Session lock service unavailable")
            if changed:
                self.bus.publish("presence.session_changed", "presence", dict(self.state))
            return
        self.idle_supported = idle is not None
        self.state.update(idle_seconds=idle, idle_supported=self.idle_supported, lock_backend=backend)
        self.observe_lock(locked)

    async def run(self):
        from .session_lock import SessionLockMonitor
        monitor = SessionLockMonitor(self.observe_session)
        task = asyncio.create_task(monitor.run(), name="session-lock-monitor")
        try:
            while True:
                if hasattr(time, "CLOCK_BOOTTIME"):
                    self.observe_clock(time.clock_gettime(time.CLOCK_BOOTTIME), time.monotonic())
                if self.state.get("session") == "UNLOCKED" and self.config.get("hand_presence", True):
                    from .tools.holohand import status
                    try:
                        self.observe_hand(await status({}, None))
                    except (OSError, ValueError):
                        self.observe_hand({})
                await asyncio.sleep(5)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
