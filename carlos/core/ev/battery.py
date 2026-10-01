"""Read power state without changing charging policy."""
from pathlib import Path
import math
import psutil


def text(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def number(path, scale=1):
    try:
        value = float(text(path)) / scale
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def read_battery(root=Path('/sys/class/power_supply')):
    try:
        summary = psutil.sensors_battery()
    except (OSError, NotImplementedError):
        summary = None
    if summary is None:
        return None
    cells = []
    for supply in sorted(root.glob('*')):
        if text(supply / 'type') != 'Battery' or text(supply / 'scope') == 'Device':
            continue
        if text(supply / 'present') == '0':
            continue
        status = text(supply / 'status')
        if status not in {'Charging', 'Discharging', 'Not charging', 'Full'}:
            status = 'Unknown'
        power = number(supply / 'power_now', 1_000_000)
        source = 'power_now' if power is not None else None
        if power is None:
            current = number(supply / 'current_now', 1_000_000)
            voltage = number(supply / 'voltage_now', 1_000_000)
            if current is not None and voltage is not None and voltage > 0:
                power, source = round(current * voltage, 4), 'current_now * voltage_now'
        cells.append({'status': status, 'power_watts': power, 'power_source': source,
                      'temperature_celsius': number(supply / 'temp', 10)})
    states = {cell['status'] for cell in cells}
    status = next(iter(states)) if len(states) == 1 else 'Mixed' if states else 'Unknown'
    return {'percent': summary.percent, 'plugged': summary.power_plugged,
            'seconds_left': summary.secsleft, 'status': status,
            'charging': True if status == 'Charging' else False if status in {'Discharging', 'Not charging', 'Full'} else None,
            'cells': cells, 'status_source': 'kernel power_supply' if cells else 'unavailable'}
