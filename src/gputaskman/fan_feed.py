"""Optional fresh fan samples from the owner's existing thermal watcher.

Xe's tachometer interval is shared across readers. Reusing one existing sample
avoids another read resetting that interval. No processes are started here.
"""
from __future__ import annotations

from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import stat
import time

LIMIT = 8192
MAX_AGE = 6.0


def read_fan_feed(pci, *, path=None, now=None, boot_id=None, resolve_pci=None):
    path = path or os.environ.get("GPUTASKMAN_FAN_FEED")
    if not path:
        return None
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
            return None
        os.lseek(fd, max(0, info.st_size - LIMIT), os.SEEK_SET)
        lines = os.read(fd, LIMIT).decode("utf-8", "replace").splitlines()
        expected_boot = boot_id if boot_id is not None else Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        now = time.time() if now is None else now
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except ValueError:
                continue  # an in-progress append may end with an unfinished line
            if not isinstance(row, dict) or row.get("boot_id") != expected_boot:
                return None
            timestamp = datetime.fromisoformat(row.get("at", ""))
            if timestamp.tzinfo is None or not 0 <= now - timestamp.timestamp() <= MAX_AGE:
                return None
            sources = row.get("sources") or {}
            hwmon = sources.get("gpu") if isinstance(sources, dict) else None
            if not isinstance(hwmon, str) or not re.fullmatch(r"/sys/class/hwmon/hwmon[0-9]+", hwmon):
                return None
            actual_pci = resolve_pci(hwmon) if resolve_pci else (Path(hwmon) / "device").resolve().name
            if actual_pci != pci:
                return None
            rpm = row.get("gpu_fan_rpm")
            if (isinstance(rpm, bool) or not isinstance(rpm, (int, float))
                    or not math.isfinite(rpm) or rpm < 0 or int(rpm) != rpm):
                return None
            return {"rpm": int(rpm), "taken": timestamp.timestamp(),
                    "source": f"GPU thermal watcher: {hwmon}/fan1_input at {row['at']} (shared driver sample)"}
    except (OSError, ValueError, TypeError, OverflowError):
        return None
    finally:
        if fd is not None:
            os.close(fd)
    return None
