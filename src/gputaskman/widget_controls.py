"""Desktop preferences, visual alarms and explicit Intel fan-control commands.

No writes to hardware occur here. Only the GUI's Apply action may launch the
already-installed administrator fan helper; collection remains read-only.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import tempfile

METRIC_SETTINGS = {
    "vram_pct": ("Video memory", "%", 100, 90),
    "util_gpu": ("GPU load", "%", 100, 95),
    "temp": ("GPU temperature", "°C", 150, 85),
    "power_w": ("Board power", "W", 2000, 200),
    "sm_clock": ("GPU clock", "MHz", 10000, 2400),
    "fan_rpm": ("Fan speed", "rpm", 20000, 1000),
    "vram_temp": ("Memory temperature", "°C", 150, 90),
}


def validate_setting(key, value):
    if key not in METRIC_SETTINGS or not isinstance(value, dict):
        raise ValueError("Unknown reading setting")
    color = value.get("color")
    alarm_color = value.get("alarm_color", "#c01c28")
    if not isinstance(alarm_color, str):
        raise ValueError("Alarm color must be a hexadecimal RGB value")
    for candidate in (color, alarm_color):
        if candidate is not None and not re.fullmatch(r"#[0-9a-fA-F]{6}", str(candidate)):
            raise ValueError("Colors must be six-digit hexadecimal RGB values")
    threshold = value.get("threshold", METRIC_SETTINGS[key][3])
    if isinstance(threshold, bool) or not isinstance(threshold, (float, int)) or not math.isfinite(threshold):
        raise ValueError("Alarm threshold must be a finite number")
    if not 0 <= threshold <= METRIC_SETTINGS[key][2]:
        raise ValueError("Alarm threshold is outside this reading's range")
    direction = value.get("direction", "below" if key == "fan_rpm" else "above")
    if direction not in ("above", "below"):
        raise ValueError("Alarm direction must be above or below")
    enabled = value.get("alarm_enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("Alarm enabled must be true or false")
    return {"color": color, "alarm_color": alarm_color,
            "alarm_enabled": enabled,
            "threshold": threshold, "direction": direction}


def alarm_state(value, setting):
    if not setting.get("alarm_enabled"):
        return "off"
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "unknown"
    threshold = setting["threshold"]
    breached = value >= threshold if setting["direction"] == "above" else value <= threshold
    return "alarm" if breached else "normal"


class Preferences:
    def __init__(self, path=None):
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        self.path = Path(path) if path else base / "gputaskman/widget.json"
        self.data = {"version": 1, "gpus": {}}
        try:
            raw = json.loads(self.path.read_text())
            if raw.get("version") == 1 and isinstance(raw.get("gpus"), dict):
                for gpu, metrics in raw["gpus"].items():
                    if not isinstance(metrics, dict):
                        continue
                    for key, setting in metrics.items():
                        try:
                            self.data["gpus"].setdefault(gpu, {})[key] = validate_setting(key, setting)
                        except ValueError:
                            pass
        except (OSError, ValueError, AttributeError):
            pass

    def get(self, gpu, key):
        raw = self.data["gpus"].get(gpu, {}).get(key, {})
        return validate_setting(key, raw)

    def set(self, gpu, key, setting):
        validated = validate_setting(key, setting)
        old = json.loads(json.dumps(self.data))
        self.data["gpus"].setdefault(gpu, {})[key] = validated
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".widget-", dir=self.path.parent)
            try:
                with os.fdopen(descriptor, "w") as stream:
                    json.dump(self.data, stream, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        except OSError:
            self.data = old
            raise


def fan_state(pci, *, sysfs=Path("/sys/bus/pci/devices")):
    if not isinstance(pci, str) or not re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]", pci):
        return {"supported": False, "reason": "Fan control needs a matched PCI device."}
    root = Path(sysfs) / pci
    for hwmon in sorted((root / "hwmon").glob("hwmon*")):
        try:
            if hwmon.joinpath("name").read_text().strip() != "xe":
                continue
            mode = hwmon / "pwm1_enable"
            points = sum((hwmon / f"pwm1_auto_point{i}_pwm").exists() for i in range(1, 11))
            if not mode.exists() or points < 4:
                return {"supported": False, "pci": pci,
                        "reason": "This xe driver reports fan RPM but does not expose fan adjustment. Fan control requires the xe fan-control driver interface."}
            try:
                current_mode = int(mode.read_text().strip())
            except (OSError, ValueError):
                current_mode = None
            return {"supported": True, "pci": pci, "mode": current_mode,
                    "mode_name": {0: "Full speed", 1: "Custom curve", 2: "Automatic"}.get(current_mode, "Not readable")}
        except OSError:
            continue
    return {"supported": False, "pci": pci, "reason": "No supported Intel xe fan controller was found for this GPU."}


def fan_command(state, mode, percent=50):
    if not state.get("supported"):
        raise ValueError(state.get("reason", "Fan control is unavailable"))
    pci = state.get("pci", "")
    if not re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]", pci):
        raise ValueError("Fan control needs a valid matched PCI address")
    if mode == "automatic":
        arguments = ["auto"]
    elif mode == "full":
        arguments = ["max"]
    elif mode == "custom":
        if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not math.isfinite(percent) or not 30 <= percent <= 100:
            raise ValueError("Fan floor must be between 30 and 100 percent")
        floor = round(percent * 255 / 100)
        # Retain a thermal ramp even with a custom fan floor.
        arguments = ["set", f"40:{floor}", f"60:{max(floor, 180)}", f"75:{max(floor, 225)}", "85:255"]
    else:
        raise ValueError("Unknown fan control mode")
    return ["/usr/bin/pkexec", "/usr/bin/env", f"ARC_GPU_BDF={pci}",
            "/usr/local/bin/xe-fan-curve", *arguments]
