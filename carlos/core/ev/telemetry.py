from __future__ import annotations

import asyncio
import math
import os
import time
from pathlib import Path
from typing import Any

import psutil

from .events import PhaxEventBus
from .hardware_metrics import HardwareMetrics, entries, read_int, read_text
from .priority import priority_for


def read_temperature(hwmon_root: Path | None = None) -> dict[str, Any]:
    from .platform import IS_FREEBSD

    if IS_FREEBSD and hwmon_root is None:
        from .platform.system import temperature

        return temperature()
    candidates: list[tuple[str, float]] = []
    for hwmon in entries(hwmon_root or Path("/sys/class/hwmon"), "hwmon[0-9]*", 64):
        name = read_text(hwmon / "name")
        if name is None or name.casefold() not in {"coretemp", "k10temp", "cpu_thermal", "cpu-thermal", "x86_pkg_temp"}:
            continue
        for input_file in entries(hwmon, "temp[0-9]*_input", 32):
            channel = input_file.name.removesuffix("_input")
            if (read_int(hwmon / (channel + "_fault"), 1) == 1
                    or read_int(hwmon / (channel + "_enable"), 1) == 0):
                continue
            try:
                value = float(read_text(input_file)) / 1000.0
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value) or value < -273.15:
                continue
            label_file = input_file.with_name(input_file.name.replace("_input", "_label"))
            label = read_text(label_file) or input_file.stem
            candidates.append((f"{name}:{label}", value))
    preferred = [item for item in candidates if item[0].casefold().startswith("coretemp:package")
                 or item[0].casefold() == "k10temp:tdie"]
    if preferred:
        candidates = preferred
    if candidates:
        sensor, value = max(candidates, key=lambda item: item[1])
        control = sensor.casefold() in {"k10temp:tctl", "k10temp:temp1_input"}
        return {"celsius": round(value, 1), "sensor": sensor,
                "measurement": "CPU_CONTROL" if control else "CPU_TEMPERATURE"}
    return {"celsius": None, "sensor": None}


class TelemetrySampler:
    def __init__(
        self, bus: PhaxEventBus, interval: float = 3.0, config: dict[str, Any] | None = None,
        hardware: HardwareMetrics | None = None,
    ) -> None:
        self.bus = bus
        self.interval = max(1.0, interval)
        self.config = config or {}
        self.hardware = hardware or HardwareMetrics()
        self.process = psutil.Process(os.getpid())
        self._last_network = psutil.net_io_counters()
        self._last_network_time = time.monotonic()
        self._last_thermal_warning: float | None = None
        self._thermal_warning_times: dict[str, float] = {}
        self._resource_mode = "NORMAL"
        self._last_link = None
        self._last_power = None
        psutil.cpu_percent(interval=None)
        self.process.cpu_percent(interval=None)

    def sample(self) -> dict[str, Any]:
        now = time.monotonic()
        memory = psutil.virtual_memory()
        available_percent = memory.available / max(1, memory.total) * 100
        critical = float(self.config.get("critical_available_percent", 8.0))
        conservation = float(self.config.get("conservation_available_percent", 15.0))
        resource_mode = (
            "CRITICAL"
            if available_percent <= critical
            else "CONSERVATION" if available_percent <= conservation else "NORMAL"
        )
        swap = psutil.swap_memory()
        root = psutil.disk_usage("/")
        net = psutil.net_io_counters()
        elapsed = max(0.001, now - self._last_network_time)
        interfaces = psutil.net_if_stats()
        linked = sum(1 for name, stats in interfaces.items()
                     if stats.isup and name not in {"lo", "lo0"}
                     and "loopback" not in getattr(stats, "flags", ""))
        network = {
            "download_bytes_per_second": max(0, round((net.bytes_recv - self._last_network.bytes_recv) / elapsed)),
            "upload_bytes_per_second": max(0, round((net.bytes_sent - self._last_network.bytes_sent) / elapsed)),
            "connected_interfaces": linked,
            "link_state": "UP" if linked else "DOWN",
            "internet_reachability": "UNVERIFIED",
        }
        self._last_network = net
        self._last_network_time = now
        from .battery import read_battery
        battery = read_battery()
        with self.process.oneshot():
            own = {
                "pid": self.process.pid,
                "rss_bytes": self.process.memory_info().rss,
                "cpu_percent": round(self.process.cpu_percent(interval=None), 2),
                "threads": self.process.num_threads(),
            }
        return {
            "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
            "cpu_temperature": read_temperature(),
            "hardware": self.hardware.sample(),
            "memory": {
                "used_bytes": memory.used,
                "available_bytes": memory.available,
                "total_bytes": memory.total,
                "percent": memory.percent,
            },
            "resource_mode": resource_mode,
            "swap": {"used_bytes": swap.used, "total_bytes": swap.total, "percent": swap.percent},
            "disk": {
                "used_bytes": root.used,
                "free_bytes": root.free,
                "total_bytes": root.total,
                "percent": root.percent,
            },
            "network": network,
            "battery": battery,
            "uptime_seconds": round(time.time() - psutil.boot_time()),
            "ev_core": own,
        }

    def observe_transitions(self, sample):
        link = sample["network"]["link_state"]
        battery = sample.get("battery")
        power = ("EXTERNAL" if battery["plugged"] else "BATTERY") if battery else "UNAVAILABLE"
        for previous, current, event in (
            (self._last_link, link, "system.network_link_changed"),
            (self._last_power, power, "system.power_source_changed"),
        ):
            if previous is not None and previous != current:
                self.bus.publish(event, "telemetry", {"from": previous, "to": current,
                                 "observed_at": time.time(), "poll_interval_seconds": self.interval})
        self._last_link, self._last_power = link, power

    def observe_thermal(self, temperature: Any, now: float | None = None) -> None:
        try:
            valid = isinstance(temperature, (int, float)) and not isinstance(temperature, bool) and math.isfinite(temperature)
        except OverflowError:
            valid = False
        if not valid:
            return
        try:
            if isinstance(self.config.get("warning_temperature_celsius"), bool):
                raise ValueError("Invalid warning threshold")
            threshold = float(self.config.get("warning_temperature_celsius", 90.0))
            if not math.isfinite(threshold):
                raise ValueError("Invalid warning threshold")
        except (TypeError, ValueError, OverflowError):
            threshold = 90.0
        try:
            if isinstance(self.config.get("warning_repeat_seconds"), bool):
                raise ValueError("Invalid warning interval")
            repeat = float(self.config.get("warning_repeat_seconds", 120.0))
            if not math.isfinite(repeat):
                raise ValueError("Invalid warning interval")
            repeat = max(0.0, repeat)
        except (TypeError, ValueError, OverflowError):
            repeat = 120.0
        if temperature < threshold:
            return
        now = time.monotonic() if now is None else now
        priority = priority_for("system.warning", "telemetry", {"kind": "thermal", "celsius": temperature})
        previous = self._thermal_warning_times.get(priority)
        if previous is not None and now - previous < repeat:
            return
        self.bus.publish(
            "system.warning", "telemetry",
            {"kind": "thermal", "message": f"Reported CPU sensor temperature is {temperature:.0f} degrees",
             "celsius": temperature, "threshold": threshold},
        )
        self._thermal_warning_times[priority] = now
        self._last_thermal_warning = now

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            started = time.perf_counter()
            try:
                sample = await asyncio.to_thread(self.sample)
                self.bus.publish(
                    "system.telemetry",
                    "telemetry",
                    sample,
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
                self.observe_transitions(sample)
                resource_mode = str(sample["resource_mode"])
                if resource_mode != self._resource_mode:
                    previous = self._resource_mode
                    self._resource_mode = resource_mode
                    self.bus.publish(
                        "system.resource_mode_changed",
                        "telemetry",
                        {
                            "from": previous,
                            "to": resource_mode,
                            "available_bytes": sample["memory"]["available_bytes"],
                        },
                    )
                self.observe_thermal(sample["cpu_temperature"].get("celsius"))
            except Exception as error:
                self.bus.publish("system.error", "telemetry", {"message": str(error)})
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.interval)
            except TimeoutError:
                pass
