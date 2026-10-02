"""Compare observations, keep missing evidence unknown, leave repairs to the user."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import tempfile
import time
from copy import deepcopy

from .security_sources import SecuritySources


class SecurityMonitor:
    def __init__(self, center, bus, config, baseline_path, privacy_token, sources=None):
        self.sources = sources or SecuritySources(center)
        self.bus = bus
        self.config = config
        self.path = baseline_path
        self.privacy_token = privacy_token
        self.interval = 60
        self.previous = {}
        self.observed = {}
        self._heavy_due = {"smart": 0.0, "updates": 0.0}
        self._reported = set()
        self._last_poll = None
        self._baseline_status = "NOT_LOADED"
        self._started = False

    def allowed(self, token=None):
        token = self.privacy_token() if token is None else token
        return token[0] not in {"PRIVATE SESSION", "GUEST"} and not token[1]

    def load_baseline(self):
        try:
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            fd = os.open(self.path, flags)
            with os.fdopen(fd, "r") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 131072:
                    raise ValueError("Not a private baseline")
                data = json.loads(stream.read(131073))
            if not isinstance(data, dict):
                raise ValueError("Invalid baseline")
            items = data["startup"]
            if data.get("version") != 1 or not isinstance(items, dict) or len(items) > 512:
                raise ValueError("Invalid baseline")
            if any(not isinstance(k, str) or not isinstance(v, str) or len(k) != 64 or len(v) != 64
                   or any(c not in "0123456789abcdef" for c in k + v) for k, v in items.items()):
                raise ValueError("Invalid fingerprints")
            self.previous["startup"] = {"status": "OK", "items": items}
            self._baseline_status = "LOADED"
        except FileNotFoundError:
            self._baseline_status = "NEW"
        except (OSError, ValueError, KeyError, TypeError):
            self._baseline_status = "UNAVAILABLE"

    def save_baseline(self, items):
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".security-baseline-", dir=self.path.parent)
            with os.fdopen(fd, "w") as stream:
                json.dump({"version": 1, "startup": items}, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self._baseline_status = "SAVED"
        except OSError:
            self._baseline_status = "UNAVAILABLE"
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def report(self, kind, count, message, high=False):
        self.bus.publish("security.alert" if high else "security.observed", "security_monitor",
                         {"kind": kind, "count": count, "message": message,
                          "severity": "HIGH" if high else "INFO", "observed_at": time.time()})

    def accept(self, name, result):
        self.observed[name] = {key: value for key, value in result.items() if key not in {"items", "scope"}}
        partial_smart = name == "smart" and result.get("status") == "INCOMPLETE"
        if result.get("status") != "OK" and not partial_smart:
            return
        old = self.previous.get(name)
        items = result.get("items", {})
        before = old.get("items", {}) if old else {}
        if name == "tailscale" and old and old.get("scope") != result.get("scope"):
            old, before = None, {}
        added = items.keys() - before.keys()
        if old and name in {"listeners", "tailscale", "failed_logins"} and added:
            descriptions = {"listeners": "New network-bound listening sockets observed; this may be a normal app change",
                            "tailscale": "Newly visible Tailscale devices; visibility does not prove a new account enrollment",
                            "failed_logins": "New failed login records observed; failed attempts alone do not prove an intrusion"}
            self.report(name, len(added), descriptions[name])
        if old and name == "startup":
            changed = {key for key in items.keys() & before.keys() if items[key] != before[key]}
            count = len(added) + len(before.keys() - items.keys()) + len(changed)
            if count:
                self.report(name, count, "Startup files changed since the last observation; review expected installations before treating this as suspicious")
        if old and name == "firewall" and items != before:
            if old.get("source") == result.get("source"):
                self.report(name, 1, "Observed firewall rules or service state changed; current protection is still subject to the source's coverage limits")
        if name in {"services", "smart", "updates"}:
            configured = self.config.get("critical_services", [])
            critical = {name for name in configured[:128] if isinstance(name, str)} if isinstance(configured, list) else set()
            warnings = {key for key, value in items.items() if value in {"FAILED", "CRASHED", "REVIEW"}}
            for key in sorted(warnings):
                stamp = (name, key)
                if stamp in self._reported:
                    continue
                high = name == "smart" or (name == "services" and key.removesuffix(".service") in critical)
                messages = {"services": f"Service {key} is marked failed or crashed; Carlos did not restart it",
                            "smart": "A readable SMART health check reports failure; back up important files",
                            "updates": "A local security advisory applies to installed packages; review it before updating"}
                self.report(name, 1, messages[name], high=high)
                self._reported.add(stamp)
            self._reported = {stamp for stamp in self._reported if stamp[0] != name or stamp[1] in warnings
                              or (partial_smart and items.get(stamp[1]) == "UNAVAILABLE")}
        if name == "startup" and (not old or items != before):
            self.save_baseline(items)
        if not partial_smart:
            self.previous[name] = deepcopy(result)

    async def poll(self):
        token = self.privacy_token()
        if not self.allowed(token) or not self.config.get("enabled", True):
            return
        if not self._started:
            self.load_baseline()
            self._started = True
        now = time.monotonic()
        probes = {name: getattr(self.sources, name)() for name in
                  ("listeners", "tailscale", "failed_logins", "services", "firewall")}
        probes["startup"] = asyncio.to_thread(self.sources.startup)
        heavy = {}
        for name, interval in (("smart", 900), ("updates", 3600)):
            if now >= self._heavy_due[name]:
                probes[name] = getattr(self.sources, name)()
                heavy[name] = now + interval
        results = await asyncio.gather(*probes.values(), return_exceptions=True)
        if token != self.privacy_token() or not self.allowed() or not self.config.get("enabled", True):
            return
        self._heavy_due.update(heavy)
        for name, result in zip(probes, results):
            if isinstance(result, BaseException):
                result = {"status": "UNAVAILABLE", "source": name}
            self.accept(name, result)
        self._last_poll = time.time()
        self.bus.publish("security.monitor_status", "security_monitor", self.snapshot())

    def snapshot(self):
        if not self.allowed():
            return {"state": "PAUSED", "reason": "Privacy mode", "sources": {}}
        return {"state": "DISABLED" if not self.config.get("enabled", True) else "MONITORING" if self._last_poll else "STARTING",
                "enabled": bool(self.config.get("enabled", True)), "poll_interval_seconds": self.interval,
                "observed_at": self._last_poll, "startup_baseline": self._baseline_status,
                "sources": deepcopy(self.observed),
                "limitations": ["Best-effort local monitoring, not an intrusion detector; unreadable sources stay unknown",
                                "First observations establish listener/device/login baselines; transient changes between polls may be missed"]}

    async def run(self, stop_event):
        await asyncio.sleep(10)
        while not stop_event.is_set():
            await self.poll()
            try:
                await asyncio.wait_for(stop_event.wait(), self.interval)
            except TimeoutError:
                pass
