"""Read-only Xe device memory queries.

The query layouts are the public drm/xe_drm.h ABI. No driver library, workload,
register writes, device setting changes or privilege escalation is involved.
"""
from __future__ import annotations

import ctypes
import errno
import os
import re
import struct
from pathlib import Path

QUERY = struct.Struct("=QIIQ2Q")
IOCTL_DEVICE_QUERY = 0xC0286440  # DRM_IOWR(DRM_COMMAND_BASE, struct drm_xe_device_query)
REGION = struct.Struct("=HHI4Q6Q")


def perfmon_capable(status: str = "/proc/self/status") -> bool:
    """The memory query silently returns used=0 without either effective capability."""
    try:
        text = Path(status).read_text()
        bits = int(re.search(r"^CapEff:\s*([0-9a-fA-F]+)$", text, re.M)[1], 16)
        return bool(bits & ((1 << 38) | (1 << 21)))  # CAP_PERFMON / CAP_SYS_ADMIN
    except (OSError, ValueError, TypeError):
        return False


class XeDevice:
    def __init__(self, sysfs: str, pdev: str, *, dev_root: str = "/dev/dri") -> None:
        self.sysfs = Path(sysfs)
        self.pdev = pdev
        self.dev_root = Path(dev_root)
        self.fd: int | None = None
        self.query_error = ""
        self._open_attempted = False

    def query(self, kind: int) -> bytes | None:
        try:
            import fcntl  # import-safe on Windows and macOS

            if not self._open_attempted:
                self._open_attempted = True
                nodes = sorted(p.name for p in (self.sysfs / "drm").iterdir()
                               if re.fullmatch(r"renderD\d+", p.name))
                if len(nodes) != 1:
                    self.query_error = "no unique render node for this PCI device"
                    return None
                self.fd = os.open(self.dev_root / nodes[0], os.O_RDONLY | os.O_CLOEXEC)
            if self.fd is None:
                return None
            request = bytearray(QUERY.pack(0, kind, 0, 0, 0, 0))
            fcntl.ioctl(self.fd, IOCTL_DEVICE_QUERY, request, True)
            size = QUERY.unpack(request)[2]
            if not 8 <= size <= 1 << 20:
                self.query_error = "invalid device-query size"
                return None
            data = ctypes.create_string_buffer(size)
            QUERY.pack_into(request, 0, 0, kind, size, ctypes.addressof(data), 0, 0)
            fcntl.ioctl(self.fd, IOCTL_DEVICE_QUERY, request, True)
            actual = QUERY.unpack(request)[2]
            if not 8 <= actual <= size:
                self.query_error = "device-query size changed"
                return None
            self.query_error = ""
            return data.raw[:actual]
        except (OSError, ImportError) as error:
            self.query_error = ("render-node access denied" if getattr(error, "errno", None)
                                in (errno.EACCES, errno.EPERM) else "device query unavailable")
            return None


    def memory(self) -> tuple[int | None, int | None]:
        data = self.query(1)
        if data is None:
            return None, None
        count = struct.unpack_from("=I", data)[0]
        if not 0 < count <= 4096 or len(data) != 8 + count * REGION.size:
            self.query_error = "invalid memory-query layout"
            return None, None
        regions = []
        seen = set()
        for index in range(count):
            cls, instance, _page, total, used, _visible, _visible_used, *_reserved = REGION.unpack_from(
                data, 8 + index * REGION.size)
            if cls != 1:  # system RAM is not dedicated device VRAM
                continue
            if instance in seen or not total or used > total:
                self.query_error = "invalid VRAM region"
                return None, None
            seen.add(instance)
            regions.append((total, used))
        if not regions:
            return None, None
        total = sum(region[0] for region in regions)
        # Without the capability even an apparently valid zero is deliberately hidden.
        used = sum(region[1] for region in regions) if perfmon_capable() else None
        return total, used


    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
