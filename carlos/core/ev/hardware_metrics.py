from __future__ import annotations

import itertools
import re
import threading
import time
from pathlib import Path


def read_text(path):
    try:
        with path.open() as stream:
            return stream.read(4096).strip()
    except (OSError, UnicodeError):
        return None


def read_int(path, maximum=None):
    text = read_text(path)
    if text is None or not re.fullmatch(r"[0-9]+", text):
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    return value if maximum is None or value <= maximum else None


def entries(path, pattern, limit):
    try:
        return sorted(itertools.islice(path.glob(pattern), limit))
    except OSError:
        return []


class HardwareMetrics:
    def __init__(self, sysfs=Path('/sys'), clock=time.monotonic):
        self.sysfs = Path(sysfs)
        self.clock = clock
        self._previous = {}
        self._lock = threading.Lock()

    def frequencies(self):
        policies = []
        for policy in entries(self.sysfs / 'devices/system/cpu/cpufreq', 'policy[0-9]*', 256):
            source, frequency = None, None
            for name in ('cpuinfo_cur_freq', 'cpuinfo_avg_freq', 'scaling_cur_freq'):
                value = read_int(policy / name)
                if value:
                    source, frequency = name, round(value / 1000, 3)
                    break
            cpus = read_text(policy / 'related_cpus')
            policies.append({
                'policy': policy.name,
                'cpus': [int(n) for n in (cpus or '').split() if n.isdecimal()][:256],
                'current_mhz': frequency,
                'source': source,
                'governor': read_text(policy / 'scaling_governor'),
                'driver': read_text(policy / 'scaling_driver'),
            })
        available = sum(p['current_mhz'] is not None for p in policies)
        return {'status': self.status(available, len(policies)), 'policies': policies,
                'note': 'Scaling frequency may be a requested state rather than an exact hardware clock.'}

    @staticmethod
    def status(available, total):
        return 'AVAILABLE' if available and available == total else 'PARTIAL' if available else 'UNAVAILABLE'

    def throttling(self, now):
        counters, previous = [], {}
        for cpu in entries(self.sysfs / 'devices/system/cpu', 'cpu[0-9]*', 256):
            for kind in ('core', 'package'):
                path = cpu / 'thermal_throttle' / (kind + '_throttle_count')
                count = read_int(path, (1 << 64) - 1)
                if count is None:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                identity = (stat.st_dev, stat.st_ino)
                key = (cpu.name, kind)
                old = self._previous.get(key)
                delta, interval, reset = None, None, False
                if old is not None:
                    old_count, old_identity, sampled_at = old
                    reset = identity != old_identity or count < old_count or now <= sampled_at
                    if not reset:
                        delta, interval = count - old_count, round(now - sampled_at, 3)
                previous[key] = (count, identity, now)
                counters.append({'cpu': cpu.name, 'kind': kind, 'count': count,
                                 'delta': delta, 'interval_seconds': interval, 'baseline_reset': reset})
        self._previous = previous
        deltas = [c['delta'] for c in counters]
        observed = True if any(d is not None and d > 0 for d in deltas) else (
            False if deltas and all(d is not None for d in deltas) else None)
        return {'status': 'AVAILABLE' if counters else 'UNAVAILABLE', 'counters': counters,
                'events_observed': observed, 'currently_throttling': None,
                'note': 'Counters record past thermal events. Package counters repeat per CPU; do not sum them.'}

    def fans(self):
        readings = []
        for hwmon in entries(self.sysfs / 'class/hwmon', 'hwmon[0-9]*', 64):
            name = read_text(hwmon / 'name') or hwmon.name
            for fan in entries(hwmon, 'fan[0-9]*_input', 16):
                channel = fan.name.removesuffix('_input')
                fault = read_int(hwmon / (channel + '_fault'), 1)
                enabled = read_int(hwmon / (channel + '_enable'), 1)
                rpm = read_int(fan) if fault != 1 and enabled != 0 else None
                readings.append({'sensor': name, 'channel': channel,
                                 'label': read_text(hwmon / (channel + '_label')),
                                 'rpm': rpm, 'fault': fault, 'enabled': enabled})
        available = sum(r['rpm'] is not None for r in readings)
        return {'status': self.status(available, len(readings)), 'readings': readings}

    def gpu_usage(self):
        devices = []
        for card in entries(self.sysfs / 'class/drm', 'card[0-9]*', 16):
            if re.fullmatch(r'card[0-9]+', card.name) is None:
                continue
            device = card / 'device'
            try:
                driver = (device / 'driver').resolve(strict=True).name
            except (OSError, RuntimeError):
                driver = None
            devices.append({'card': card.name, 'driver': driver,
                            'busy_percent': read_int(device / 'gpu_busy_percent', 100),
                            'memory_busy_percent': read_int(device / 'mem_busy_percent', 100)})
        available = sum(d['busy_percent'] is not None for d in devices)
        return {'status': self.status(available, len(devices)), 'devices': devices,
                'note': 'Only driver-provided sysfs load readings are available; missing data is not zero load.'}

    def sample(self):
        with self._lock:
            now = self.clock()
            return {'observed_at': time.time(), 'cpu_frequency': self.frequencies(),
                    'cpu_throttling': self.throttling(now), 'fans': self.fans(),
                    'gpu_usage': self.gpu_usage()}
