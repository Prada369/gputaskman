"""Desktop snapshot presentation and a budgeted JSON stream; no GTK dependency."""
from __future__ import annotations

import argparse
import json
from functools import lru_cache
from contextlib import contextmanager, ExitStack
from dataclasses import asdict
import math
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path, PurePath
import re
import shlex
import signal
import subprocess
import time

PRODUCT_SOURCE = "https://www.asrock.com/Graphics-Card/Intel/Intel%20Arc%20Pro%20B65%20Creator%2032GB/"


def product_info(hardware):
    # Exact board identity observed on the owner's B65, confirmed by the owner.
    # Do not rename every G31 or every Intel adapter based on a generic chip name.
    identity = tuple(str(hardware.get(key, "")).lower() for key in
                     ("vendor", "device", "subsystem_vendor", "subsystem_device"))
    if identity != ("0x8086", "0xe222", "0x1849", "0x6027"):
        return {}
    return {"name": "Intel Arc Pro B65", "board": "ASRock · Creator 32GB",
            "rated_power_w": 200,
            "image": "asrock-b65-creator.png", "source": PRODUCT_SOURCE,
            "specs": {"GPU product": "Intel Arc Pro B65", "Board maker": "ASRock",
                      "Architecture": "Xe2-HPG · Battlemage G31", "Memory capacity": "32 GB GDDR6",
                      "Memory interface": "256-bit", "Memory data rate": "19 Gbps",
                      "Memory bandwidth": "608 GB/s (rated)", "Graphics clock": "2,400 MHz (rated)",
                      "Xe cores": "20", "XMX AI engines": "160", "Ray tracing units": "20",
                      "PCIe support": "PCIe 5.0 ×16 (capability)", "Display outputs": "4 × DisplayPort 2.1",
                      "Board power": "200 W (reference rating)"}}


READING_TITLES = {"mem_total": "Usable VRAM", "mem_used": "Used VRAM", "mem_reserved": "Reserved VRAM",
    "util_gpu": "GPU load", "util_mem": "Memory activity", "power_w": "Board power",
    "power_limit_w": "Power limit", "temp": "GPU temperature", "vram_temp": "VRAM temperature",
    "sm_clock": "GPU clock", "sm_clock_max": "Maximum GPU clock", "gfx_clock": "Graphics clock",
    "mem_clock": "Memory clock", "video_clock": "Video clock", "fan_rpm": "Fan speed",
    "fan": "Fan percentage", "pcie_rx_kbs": "PCIe receive", "pcie_tx_kbs": "PCIe transmit",
    "pcie_gen": "Live PCIe generation", "pcie_width": "Live PCIe width", "video": "Video engine load",
    "mem_read_bps": "Memory read traffic", "mem_write_bps": "Memory write traffic",
    "mem_bandwidth_max_bps": "Reported bandwidth ceiling", "mem_bandwidth_pct": "Bandwidth use",
    "enc": "Encoder load", "dec": "Decoder load", "pstate": "P-state", "compute_cap": "CUDA capability"}


def reading_caption(device, key):
    if key == "temp" and device.get("temp_kind"):
        return f"Temperature ({device['temp_kind']})"
    if key == "power_w" and device.get("power_kind") not in (None, "", "Board"):
        return f"Power ({device['power_kind']})"
    title = READING_TITLES[key]
    if key in ("util_gpu", "video") and device.get("util_kind" if key == "util_gpu" else "video_kind"):
        title += " (estimated)"
    return title


def reading_source(device, key):
    source = (device.get("metric_sources") or {}).get(key) or device.get("source") or "Device counter"
    kind = {"temp": "temp_kind", "power_w": "power_kind", "util_gpu": "util_kind", "video": "video_kind"}.get(key)
    detail = device.get(kind) if kind else None
    if detail and detail not in source:
        source += " · " + detail
    return source


class FanReadings:
    """Separate raw driver RPM from timestamped history, without replacing zeros."""
    def __init__(self):
        self.last_nonzero = {}

    def record(self, key, device, now):
        rpm = device.get("fan_rpm")
        if isinstance(rpm, (int, float)) and math.isfinite(rpm) and rpm > 0:
            self.last_nonzero[key] = (rpm, now)

    def present(self, key, device, now):
        rpm = device.get("fan_rpm")
        intel = device.get("driver_model") == "xe" or device.get("source") == "Intel sysfs"
        text = metric(rpm, " rpm")
        note = ""
        if intel and rpm == 0:
            text = "0 reported"
            note = ("The GPU driver reported 0 RPM. This does not verify that the fan stopped; "
                    "Xe calculates RPM from a sampling interval shared by sensor readers.")
            if key in self.last_nonzero:
                previous, taken = self.last_nonzero[key]
                age = max(0, int(now - taken))
                note += f" Last non-zero report: {metric(previous, ' rpm')}, {age}s ago (historical)."
        return text, note


def device_readings(snapshot):
    """Available measurements and explained gaps; no irrelevant vendor placeholders."""
    nv, adapter = snapshot.get("device") or {}, snapshot.get("adapter") or {}
    intel = adapter.get("vendor_id") == 0x8086 or nv.get("source") == "Intel sysfs"
    skip = {"util_mem", "enc", "dec", "pstate", "compute_cap", "fan"} if intel else set()
    rows, unavailable = {}, {}
    statuses = nv.get("metric_status") or {}
    for key, title in READING_TITLES.items():
        if key in skip:
            continue
        title = reading_caption(nv, key)
        value = nv.get(key)
        if value is None or value == "":
            # Optional fields with no provider have no useful empty row.
            if key in statuses:
                unavailable[title] = statuses[key]
            continue
        if key in ("mem_total", "mem_used", "mem_reserved"):
            text = memory(value)
        elif key.endswith("_bps"):
            text = metric(value / 1e9, " GB/s", 2)
        elif key.endswith("_kbs"):
            text = metric(value / 1024, " MiB/s", 2)
        elif key in ("temp", "vram_temp"):
            text = metric(value, "°C")
        elif "clock" in key:
            text = metric(value, " MHz")
        elif key in ("power_w", "power_limit_w"):
            text = metric(value, " W", 1)
        elif key == "fan_rpm":
            text = metric(value, " rpm")
        elif key in ("util_gpu", "util_mem", "video", "enc", "dec", "fan", "mem_bandwidth_pct"):
            text = metric(value, "%", 1)
        else:
            text = str(value)
        rows[title] = (text, reading_source(nv, key))
    for key, value in (nv.get("temperature_sensors_c") or {}).items():
        rows["Sensor " + key] = (metric(value, "°C"), "Intel hwmon sensor")
    if snapshot.get("shared_used") is not None:
        rows["Shared GPU memory"] = (memory(snapshot["shared_used"]), "Driver-reported")
    return rows, unavailable


def metric(value, unit="", precision=0):
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "n/a"
    if unit == "%" and precision == 1:
        decimal = Decimal(str(value))
        with localcontext() as context:
            context.prec = max(context.prec, decimal.adjusted() + 3)
            value = decimal.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{value:,.{precision}f}{unit}"


def memory(value):
    if value is None:
        return "n/a"
    return metric(value / (1 << 30), " GiB", 2)


@lru_cache(maxsize=8)
def hardware_info(pci):
    """Static PCI identity only; never substitute link maxima for live counters."""
    if not isinstance(pci, str) or not re.fullmatch(r"[0-9a-fA-F]{4,8}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]", pci):
        return {}
    domain, location = pci.split(":", 1)
    pci = f"{int(domain, 16):04x}:{location}"
    info = {}
    for field in ("vendor", "device", "subsystem_vendor", "subsystem_device"):
        try:
            info[field] = (Path("/sys/bus/pci/devices") / pci / field).read_text()[:32].strip()
        except OSError:
            pass
    try:
        result = subprocess.run(["lspci", "-Dmm", "-s", pci], capture_output=True, text=True, timeout=1)
        fields = shlex.split(result.stdout.splitlines()[0])
        if len(fields) >= 4:
            info["make"] = fields[2].removesuffix(" Corporation")
            info["model"] = re.sub(r"\s+\[.*\]$", "", fields[3])
            info["name"] = info["make"] + " " + info["model"]
            board = [field for field in fields[4:] if not field.startswith("-")]
            if len(board) >= 2:
                info["board_make"] = board[0].removesuffix(" Incorporation").removesuffix(" Corporation")
                info["board_model"] = board[1]
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        pass
    return info


def gpu_identity(snapshot):
    hardware, adapter = snapshot.get("hardware") or {}, snapshot.get("adapter") or {}
    nv = snapshot.get("device") or {}
    make = {0x8086: "Intel", 0x10de: "NVIDIA", 0x1002: "AMD", 0x106b: "Apple",
            0x19e5: "Huawei"}.get(adapter.get("vendor_id"), hardware.get("make") or "Unknown maker")
    product = product_info(hardware)
    model = product.get("name") or hardware.get("model") or snapshot.get("gpu") or nv.get("name") or "Unknown GPU"
    return {"make": make, "model": model, "name": hardware.get("name") or model,
            "board_make": hardware.get("board_make") or "Not reported", "product": product,
            "board_model": hardware.get("board_model") or "Not reported",
            "pci": nv.get("pci_bus") or "", "index": snapshot.get("gpu_index", 0),
            "id": snapshot.get("gpu_id") or f"{adapter.get('vendor_id')}:{adapter.get('luid', nv.get('pci_bus', 'default'))}"}


def local_model_names():
    """Friendly names in existing local Ollama manifests, never a loaded claim.

    Reuses the existing store's 60-second manifest cache; no API calls or loads.
    """
    import os
    from .stores import ollama_names

    roots = [os.environ.get("OLLAMA_MODELS"), str(Path.home() / ".ollama/models"),
             str(Path.home() / "models/ollama")]
    names = set()
    for root in dict.fromkeys(root for root in roots if root):
        for values in ollama_names(root).values():
            names.update(values)
    return sorted(names, key=str.casefold)


def model_cards(snapshot):
    """Keep process allocations distinct from model files and runtime reports.

    A mapped filename proves a mapping, not how many of its bytes live on the GPU.
    Installed-only candidates never become active model cards.
    """
    cards = []
    for process in snapshot.get("processes", []):
        llm = process.get("llm") or {}
        server = llm.get("server") or {}
        names, evidence, info = [], set(), []
        for model in llm.get("models", []):
            names.append(model.get("label") or model.get("name") or PurePath(model.get("path", "")).name)
            evidence.update(model.get("sources") or [])
            metadata = [model.get("quant"), model.get("arch")]
            if model.get("params") is not None:
                metadata.append(metric(model["params"] / 1e9, "B parameters", 1))
            if model.get("layers") is not None:
                metadata.append(f"{model['layers']} layers")
            info.append(" · ".join(str(x) for x in metadata if x))
        for model in server.get("models", []):
            if model.get("name"):
                names.append(model["name"])
                evidence.add("api")
                details = [model.get("type"), model.get("quant")]
                if model.get("ctx") is not None:
                    details.append(f"{model['ctx']:,} context (runtime)")
                if model.get("size_vram") is not None:
                    details.append(f"{memory(model['size_vram'])} VRAM (runtime)")
                info.append(" · ".join(str(x) for x in details if x))
        files = process.get("model_files") or []
        for model in files:
            sources = set(model.get("sources") or [])
            evidence.update(sources)
            if not names or not llm.get("models"):
                path = PurePath(model.get("path", ""))
                name = model.get("store_name") or (path.parent.name if path.suffix == ".safetensors" else path.name)
                if name:
                    names.append(name)
        if not names and process.get("kind") not in ("AI", "LLM"):
            continue
        if not names:
            names = ["Model not identified yet"]
        confirmed = evidence & {"mapped", "api", "cmdline"}
        provenance = " · ".join({"mapped": "mapped files", "api": "runtime API", "cmdline": "command line"}[s]
                                for s in ("api", "mapped", "cmdline") if s in confirmed)
        if not provenance:
            provenance = "Path hint only; residency unconfirmed" if names != ["Model not identified yet"] else "AI runtime detected; model unknown"
        process_vram = memory(process.get("vram_bytes"))
        if process.get("vram_bytes") is None and process.get("vram_inferred_bytes") is not None:
            process_vram = memory(process["vram_inferred_bytes"]) + " (inferred owner)"
        info.insert(0, f"Process VRAM {process_vram} · GPU {metric(process.get('gpu_pct'), '%', 1)}")
        if llm.get("n_ctx") is not None:
            info.append(f"Context {llm['n_ctx']:,} tokens (runtime/launch settings)")
        elif not any(m.get("ctx") is not None for m in server.get("models", [])):
            info.append("Current context not reported")
        if server.get("gen_tok_s") is not None:
            info.append(metric(server["gen_tok_s"], " tokens/s (runtime)", 1))
        for segment in llm.get("segments", []):
            if segment.get("kind") != "unknown":
                origin = "exact" if segment.get("exact") else "estimated"
                info.append(f"{segment.get('label', 'Buffer')}: {memory(segment.get('bytes'))} ({origin})")
        if files:
            info.append(f"{len(files)} model file(s) observed; file mappings do not measure GPU residency")
        cards.append({"name": " / ".join(dict.fromkeys(names)),
                      "runtime": " · ".join(process.get("runtimes") or [process.get("name", "Process")]),
                      "pid": process.get("pid"), "evidence": provenance,
                      "details": "\n".join(dict.fromkeys(x for x in info if x)),
                      "files": "\n".join(f.get("path", "") for f in files)})
    return cards


@contextmanager
def snapshot_source(gpu=None, *, discover=None, factory=None):
    """Sample every selected adapter independently, sharing the 150 ms scan budget.

    Discovery is refreshed every ten seconds. Collector ownership follows native
    adapter identity; two cards with the same name always keep separate counters.
    """
    from .collector import Collector, enumerate_adapters, ordered_adapters
    from .report import to_json_dict
    from .widget_controls import fan_state, tuning_state

    discover = discover or enumerate_adapters
    factory = factory or Collector
    collectors, next_discovery, topology, discovery_error = {}, 0, [], None
    with ExitStack() as resources:
        def sample():
            nonlocal next_discovery, topology, discovery_error
            now = time.monotonic()
            if now >= next_discovery:
                next_discovery = now + 10
                try:
                    topology = ordered_adapters(discover())
                    discovery_error = None
                except Exception as error:
                    discovery_error = f"GPU discovery failed: {error}"
                live = {(a.vendor_id, a.luid) for a in topology}
                for key in list(collectors):
                    if key not in live:
                        collectors.pop(key).close()
            selected = list(enumerate(topology)) if gpu is None else [(gpu, topology[gpu])] if gpu < len(topology) else []
            samples = []
            for index, adapter in selected:
                key = adapter.vendor_id, adapter.luid
                try:
                    if key not in collectors:
                        collector = factory(index, adapters=topology, scan_budget=Collector.SCAN_BUDGET / max(1, len(selected)))
                        collectors[key] = collector
                        # Close by identity at context exit, even if a later sample fails.
                    collector = collectors[key]
                    collector.scan_budget = Collector.SCAN_BUDGET / max(1, len(selected))
                    drm = getattr(collector.counters, "drm", None)
                    if drm is not None:
                        drm.scan_budget = collector.scan_budget
                    snapshot = to_json_dict(collectors[key].sample())
                    snapshot["hardware"] = hardware_info((snapshot.get("device") or {}).get("pci_bus"))
                    snapshot["fan_control"] = fan_state((snapshot.get("device") or {}).get("pci_bus"))
                    snapshot["tuning"] = tuning_state((snapshot.get("device") or {}).get("pci_bus"))
                except Exception as error:
                    snapshot = {"gpu": adapter.name, "adapter": asdict(adapter), "device": None,
                                "error": f"{type(error).__name__}: {error}"}
                snapshot.update(gpu_index=index, gpu_id=f"{adapter.vendor_id}:{adapter.luid}")
                samples.append(snapshot)
            result = {"taken": time.time(), "gpus": samples}
            if discovery_error:
                result["inventory_error"] = discovery_error
            if not samples:
                result["error"] = (discovery_error or "No GPU adapters detected") if gpu is None else f"GPU {gpu} is not available"
            return result

        def close_all():
            for collector in collectors.values():
                collector.close()
            collectors.clear()

        resources.callback(close_all)
        yield sample


def stream(gpu=None, interval=2.0):

    def interrupt(_sig, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        with snapshot_source(gpu) as sample:
            while True:
                start = time.monotonic()
                try:
                    result = sample()
                    result["model_library"] = local_model_names()
                except Exception as error:
                    result = {"error": f"{type(error).__name__}: {error}"}
                print(json.dumps(result, separators=(",", ":")), flush=True)
                time.sleep(max(0, interval - (time.monotonic() - start)))
    except (KeyboardInterrupt, BrokenPipeError):
        pass


def main():
    parser = argparse.ArgumentParser(description="Read-only desktop GPU snapshot stream")
    parser.add_argument("--gpu", type=int, help="show only this adapter (default: all)")
    parser.add_argument("--interval", type=float, default=2)
    args = parser.parse_args()
    if (args.gpu is not None and args.gpu < 0) or not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("GPU must be non-negative and interval must be finite and positive")
    stream(args.gpu, args.interval)


if __name__ == "__main__":
    main()
