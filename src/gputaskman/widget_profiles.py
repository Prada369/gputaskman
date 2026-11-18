"""Local tuning profiles and bounded, explicitly measured session history."""
from __future__ import annotations

from collections import deque
import json
import math
import os
from pathlib import Path
import tempfile


def presets(state):
    low, high = state['clock_low'], state['clock_high']
    power = min(200, state.get('power_w') or 200)
    base = {'clock_min': low, 'clock_max': high, 'power_w': power,
            'profile': 'base', 'target': 80, 'floor': 60, 'guard': True,
            'guard_floor': max(1, round(power * .6)), 'workload': False}
    return {'Performance': base,
            'Quiet': dict(base, clock_max=max(low, min(high, 2000)), power_w=round(power*.75),
                          target=85, floor=40, profile='power_saving'),
            'Efficiency': dict(base, power_w=round(power*.8), target=80, floor=50,
                               profile='power_saving')}


def validate_profile(raw, state):
    result = dict(raw)
    for key in ('clock_min', 'clock_max', 'power_w', 'target', 'floor', 'guard_floor'):
        value = result.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value:
            raise ValueError(f'{key} must be a whole number')
        result[key] = int(value)
    if not state['clock_low'] <= result['clock_min'] <= result['clock_max'] <= state['clock_high']:
        raise ValueError('Profile clocks exceed the driver range')
    ceiling = state.get('power_ceiling', state.get('power_w'))
    if ceiling is None or not 1 <= result['power_w'] <= ceiling:
        raise ValueError('Profile power exceeds the displayed power range')
    if not 65 <= result['target'] <= 95 or not 40 <= result['floor'] <= 100:
        raise ValueError('Profile temperature or fan floor is outside its supported range')
    if not 1 <= result['guard_floor'] <= result['power_w']:
        raise ValueError('Thermal guard minimum must be no higher than profile power')
    if result.get('profile') not in state.get('profiles', []):
        result['profile'] = None
    if any(not isinstance(result.get(key), bool) for key in ('guard', 'workload')):
        raise ValueError('Profile switches must be true or false')
    return result


class ProfileStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'gputaskman/profiles.json'
        try:
            self.data = json.loads(self.path.read_text())
            if not isinstance(self.data, dict):
                self.data = {}
        except (OSError, ValueError):
            self.data = {}

    def get(self, pci, state):
        raw = self.data.get(pci)
        return validate_profile(raw, state) if isinstance(raw, dict) else None

    def save(self, pci, profile, state):
        data = dict(self.data, **{pci: validate_profile(profile, state)})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.profiles-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(data, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.data = data
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class WorkloadGate:
    """Only GPU load or measured busy engine rows activate a workload profile."""
    def __init__(self):
        self.last_busy = None

    def active(self, snapshot, now):
        device = snapshot.get('device') or {}
        load = device.get('util_gpu')
        busy = isinstance(load, (int, float)) and not isinstance(load, bool) and math.isfinite(load) and load >= 5
        for row in snapshot.get('processes') or []:
            busy = busy or any(isinstance(row.get(key), (int, float)) and not isinstance(row[key], bool)
                               and math.isfinite(row[key]) and row[key] >= 5
                               for key in ('gpu_pct', 'compute_pct', 'gpu'))
        if busy:
            self.last_busy = now
        return self.last_busy is not None and 0 <= now - self.last_busy <= 15


class SessionHistory:
    def __init__(self):
        self.samples = deque(maxlen=300)

    def record(self, snapshot):
        d = snapshot.get('device') or {}
        row = {key: d.get(key) for key in ('sm_clock', 'power_w', 'temp', 'vram_temp', 'util_gpu')}
        for key, value in row.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                row[key] = None
        row['taken'] = snapshot.get('taken')
        row['metric_sources'] = d.get('metric_sources') or {}
        row['util_kind'] = d.get('util_kind')
        tuning = snapshot.get('tuning') or {}
        row['settings'] = {key: tuning.get(key) for key in ('clock_min', 'clock_max', 'power_w', 'profile')}
        self.samples.append(row)

    def summary(self):
        result = {}
        for key in ('sm_clock', 'power_w', 'temp', 'vram_temp', 'util_gpu'):
            values = [r[key] for r in self.samples if r[key] is not None]
            if values:
                result[key] = {'mean': sum(values)/len(values), 'peak': max(values), 'samples': len(values)}
        return result

    def save(self, path, profile):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({'profile': profile, 'summary': self.summary(),
                                      'samples': list(self.samples),
                                      'note': 'Recent samples may span settings; per-sample sources and settings are retained. GPU load may be estimated as labeled by util_kind. No inference throughput measured.'}, indent=2))
        return target
