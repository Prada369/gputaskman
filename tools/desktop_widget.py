#!/usr/bin/python3
"""Native GTK desktop GPU snapshot, backed by the existing budgeted collector."""
from __future__ import annotations

import argparse
from collections import deque
import json
import math
import os
from pathlib import Path
import signal
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as Indicator
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gputaskman.widget import device_readings, gpu_identity, memory, metric, model_cards

CSS = b"""
window.snapshot { background: linear-gradient(150deg, #141c31, #101624 70%); color: #edf5ff;
    border: 1px solid #35415d; border-radius: 20px; }
.snapshot label { color: #e9effb; }
.snapshot .eyebrow { font-size: 10px; font-weight: bold; color: #9fafff; letter-spacing: 2px; }
.snapshot .gpu-name { font-size: 24px; font-weight: bold; letter-spacing: -0.5px; }
.snapshot .muted { color: #95a4c1; font-size: 11px; }
.snapshot .small { font-size: 10px; color: #95a4c1; }
.snapshot .live { color: #70e3c2; font-size: 10px; font-weight: bold; }
.snapshot .card { background: #1a2337; border: 1px solid #2b3853; border-radius: 12px; padding: 9px 12px; }
.snapshot .memory-card { background: linear-gradient(110deg, #243251, #1a2b39); border-color: #3d5272; }
.snapshot .memory-reading { font-size: 24px; font-weight: bold; color: #e5fbf5; }
.snapshot .reading { font-size: 24px; font-weight: bold; color: #f4f7ff; }
.snapshot .model-name { color: #c4caff; font-size: 14px; font-weight: bold; }
.snapshot .section { font-size: 10px; color: #a4b2ce; font-weight: bold; letter-spacing: 1px; }
.snapshot .warning { font-size: 11px; color: #f1c48b; }
.snapshot .badge { background: #28395a; color: #b9d4ff; padding: 4px 8px; border-radius: 6px;
    font-size: 10px; font-weight: bold; }
.snapshot .gpu-icon { color: #aabaff; background: #283552; border-radius: 12px; padding: 8px; }
.snapshot .metric-icon { color: #93a8e8; }
.snapshot .model-card { background: linear-gradient(135deg, #262b45, #1a2438); border-color: #3b4167; }
.snapshot .inventory-name { font-size: 13px; font-weight: bold; }
.snapshot button { background: #202b43; border: 1px solid #33415f; border-radius: 8px;
    color: #becae5; box-shadow: none; min-height: 22px; min-width: 22px; padding: 4px; }
.snapshot button:hover { background: #2d3b5a; color: white; }
.snapshot button:checked { color: #9cadff; background: #303f65; }
.snapshot button.gpu-option { background: #182237; border: 1px solid #2d3b58; padding: 10px 12px; border-radius: 12px; }
.snapshot button.gpu-option:hover { background: #24324c; }
.snapshot button.selected-gpu { background: linear-gradient(110deg, #293957, #20363e); border-color: #6d99bb; }
.snapshot progressbar trough { background: #36435b; border: none; border-radius: 4px; min-height: 7px; }
.snapshot progressbar progress { background: linear-gradient(to right, #92a3ff, #6bdfc0); border: none;
    border-radius: 4px; min-height: 7px; }
.snapshot expander title { color: #b4c0db; padding: 3px 0; }
.snapshot scrollbar { background: transparent; }
"""


def label(text="", style=None, wrap=False):
    widget = Gtk.Label(label=text, xalign=0)
    if style:
        widget.get_style_context().add_class(style)
    widget.set_line_wrap(wrap)
    if wrap:
        widget.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    widget.set_max_width_chars(54)
    widget.set_selectable(False)
    return widget


def box(vertical=True, spacing=6, style=None):
    widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                     spacing=spacing)
    if style:
        widget.get_style_context().add_class(style)
    return widget


class History(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.values = deque(maxlen=60)
        self.set_size_request(-1, 56)
        self.connect("draw", self.draw_history)

    def append(self, value):
        self.values.append(value)
        self.queue_draw()

    def draw_history(self, _widget, cr):
        width, height = self.get_allocated_width(), self.get_allocated_height()
        cr.set_line_width(1)
        cr.set_source_rgb(.17, .22, .32)
        for fraction in (.25, .75):
            cr.move_to(0, height * fraction)
            cr.line_to(width, height * fraction)
        cr.stroke()
        cr.set_source_rgb(.43, .87, .75)
        cr.set_line_width(1.8)
        drawing = False
        for index, value in enumerate(self.values):
            if value is None:
                cr.stroke()
                drawing = False
                continue
            x = width * (60 - len(self.values) + index) / 59
            y = height - 3 - min(100, max(0, value)) / 100 * (height - 6)
            if drawing:
                cr.line_to(x, y)
            else:
                cr.move_to(x, y)
                drawing = True
        cr.stroke()


class GpuIcon(Gtk.DrawingArea):
    """Small vector GPU-board illustration drawn by the desktop widget."""
    def __init__(self):
        super().__init__()
        self.set_size_request(44, 34)
        self.connect("draw", self.draw_icon)

    def draw_icon(self, _widget, cr):
        cr.set_source_rgb(.66, .73, 1)
        cr.set_line_width(1.6)
        cr.rectangle(2, 6, 37, 23)
        cr.stroke()
        for x in (14, 28):
            cr.arc(x, 17, 7.5, 0, 6.284)
            cr.stroke()
            for angle in (0, 2.094, 4.189):
                cr.move_to(x, 17)
                cr.line_to(x + 5 * math.cos(angle), 17 + 5 * math.sin(angle))
            cr.stroke()
        for x in range(10, 32, 4):
            cr.move_to(x, 29)
            cr.line_to(x, 33)
        cr.move_to(40, 3)
        cr.line_to(43, 3)
        cr.line_to(43, 30)
        cr.stroke()


class GpuWidget(Gtk.Application):
    def __init__(self, command, *, start_hidden=False):
        super().__init__(application_id="local.gputaskman.Widget")
        self.command, self.start_hidden = command, start_hidden
        self.worker = None
        self.stopping = False
        self.last_update = None
        self.started_at = time.monotonic()
        self.last_snapshot = None
        self.gpu_snapshots = {}
        self.selected_gpu = None
        self.gpu_rows = {}
        self.histories = {}
        self.model_signature = None
        self.detail_values = {}
        self.detail_keys = {}
        self.current_product_image = None
        self.stop_timer = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for name, callback in (("show", self.show_window), ("hide", self.hide_window),
                               ("refresh", self.start_monitor), ("quit", self.stop)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)
        self.build_window()
        self.build_indicator()
        self.start_monitor()
        self.clock_timer = GLib.timeout_add_seconds(1, self.check_freshness)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.stop)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self.stop)

    def build_window(self):
        self.window = Gtk.ApplicationWindow(application=self, title="GPU Snapshot")
        self.window.get_style_context().add_class("snapshot")
        self.window.set_decorated(False)
        self.window.set_default_size(510, 820)
        self.window.set_icon_name("video-display")
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.stick()
        self.window.connect("delete-event", self.on_close)
        self.window.connect("window-state-event", self.on_window_state)
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        work = monitor.get_workarea()
        self.window.set_default_size(510, min(850, work.height - 48))
        self.window.move(work.x + work.width - 538, work.y + 24)

        root = box(spacing=0)
        self.window.add(root)
        drag = Gtk.EventBox()
        drag.connect("button-press-event", self.on_drag)
        header = box(False, 8)
        for setter in (header.set_margin_top, header.set_margin_start, header.set_margin_end):
            setter(18)
        header.set_margin_bottom(12)
        header.pack_start(label("GPU SNAPSHOT", "eyebrow"), True, True, 0)
        self.live = label("CONNECTING", "live")
        header.pack_start(self.live, False, False, 0)
        pin = Gtk.ToggleButton()
        pin.add(Gtk.Image.new_from_icon_name("view-pin-symbolic", Gtk.IconSize.MENU))
        pin.set_tooltip_text("Keep widget above other windows")
        pin.connect("toggled", lambda button: self.window.set_keep_above(button.get_active()))
        header.pack_start(pin, False, False, 0)
        hide = Gtk.Button.new_from_icon_name("window-minimize-symbolic", Gtk.IconSize.MENU)
        self.minimize_button = hide
        hide.set_tooltip_text("Minimize to top status bar")
        hide.connect("clicked", lambda _b: self.hide_window())
        header.pack_start(hide, False, False, 0)
        drag.add(header)
        root.pack_start(drag, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content = box(spacing=11)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_bottom(12)
        scroll.add(content)
        root.pack_start(scroll, True, True, 0)
        inventory_header = box(False)
        inventory_header.pack_start(label("YOUR GPUs", "section"), True, True, 0)
        self.gpu_count = label("Detecting…", "small")
        inventory_header.pack_end(self.gpu_count, False, False, 0)
        content.pack_start(inventory_header, False, False, 0)
        self.gpu_list = box(spacing=7)
        content.pack_start(self.gpu_list, False, False, 0)
        hero = box(False, 14)
        icon = box(style="gpu-icon")
        icon.set_valign(Gtk.Align.CENTER)
        self.hero_icon = Gtk.Stack()
        self.hero_icon.add_named(GpuIcon(), "generic")
        self.product_image = Gtk.Image()
        self.hero_icon.add_named(self.product_image, "product")
        self.hero_icon.show_all()
        self.hero_icon.set_visible_child_name("generic")
        icon.add(self.hero_icon)
        hero.pack_start(icon, False, False, 0)
        hero_text = box(spacing=5)
        self.gpu_make = label("GPU", "eyebrow")
        hero_text.pack_start(self.gpu_make, False, False, 0)
        self.gpu_name = label("Reading hardware…", "gpu-name", True)
        hero_text.pack_start(self.gpu_name, False, False, 0)
        self.board_name = label("", "muted", True)
        hero_text.pack_start(self.board_name, False, False, 0)
        hero.pack_start(hero_text, True, True, 0)
        content.pack_start(hero, False, False, 0)
        self.identity = label("", "muted", True)
        content.pack_start(self.identity, False, False, 0)
        self.notice = label("", "warning", True)
        self.notice.set_no_show_all(True)
        content.pack_start(self.notice, False, False, 0)

        memory_card = box(style="card")
        memory_card.get_style_context().add_class("memory-card")
        row = box(False)
        row.pack_start(label("VIDEO MEMORY", "section"), True, True, 0)
        self.vram_pct = label("—", "badge")
        row.pack_end(self.vram_pct, False, False, 0)
        memory_card.pack_start(row, False, False, 0)
        self.vram_text = label("Waiting for memory readings", "memory-reading")
        memory_card.pack_start(self.vram_text, False, False, 2)
        self.vram_bar = Gtk.ProgressBar()
        memory_card.pack_start(self.vram_bar, False, False, 2)
        self.memory_source = label("Waiting for a reading", "small", True)
        memory_card.pack_start(self.memory_source, False, False, 0)
        content.pack_start(memory_card, False, False, 0)

        metrics = Gtk.Grid(column_spacing=8, row_spacing=8)
        metrics.set_column_homogeneous(True)
        self.metrics = {}
        for index, (key, title) in enumerate((("util_gpu", "GPU LOAD"), ("temp", "GPU TEMP"),
                         ("power_w", "POWER"), ("sm_clock", "GPU CLOCK"),
                         ("fan_rpm", "FAN"), ("vram_temp", "VRAM TEMP"))):
            card = box(spacing=3, style="card")
            # Keep the controls compact so model activity fits on the desktop.
            caption = label(title, "section")
            card.pack_start(caption, False, False, 0)
            value = label("n/a", "reading")
            card.pack_start(value, False, False, 0)
            self.metrics[key] = (value, caption, card)
            metrics.attach(card, index % 3, index // 3, 1, 1)
        content.pack_start(metrics, False, False, 0)

        history = box(spacing=2)
        history.pack_start(label("VRAM TREND  ·  LAST 120 SECONDS", "section"), False, False, 0)
        self.history = History()
        history.pack_start(self.history, False, False, 0)
        content.pack_start(history, False, False, 0)

        model_heading = box(False)
        model_heading.pack_start(label("MODEL ACTIVITY", "section"), True, True, 0)
        self.model_count = label("", "small")
        model_heading.pack_end(self.model_count, False, False, 0)
        content.pack_start(model_heading, False, False, 0)
        self.models = box(spacing=8)
        self.models.pack_start(label("Looking for active model evidence…", "muted", True), False, False, 0)
        content.pack_start(self.models, False, False, 0)

        self.library_expander = Gtk.Expander(label="Local model library")
        library_body = box(spacing=8)
        library_body.set_margin_top(10)
        library_body.pack_start(label("Names from local Ollama manifests. Active models are shown above.", "small", True), False, False, 0)
        self.library_text = label("Reading local model names…", "muted", True)
        library_body.pack_start(self.library_text, False, False, 0)
        self.library_expander.add(library_body)
        content.pack_start(self.library_expander, False, False, 0)

        expander = Gtk.Expander(label="All GPU specs & sensors")
        spec_body = box(spacing=12)
        self.published_specs = box(spacing=6)
        self.published_specs.set_no_show_all(True)
        self.published_specs.pack_start(label("PUBLISHED HARDWARE SPECIFICATIONS", "section"), False, False, 0)
        self.product_specs = label("", "muted", True)
        self.published_specs.pack_start(self.product_specs, False, False, 0)
        self.published_specs.pack_start(label("Manufacturer ratings; current live readings are below.", "small", True), False, False, 0)
        spec_body.pack_start(self.published_specs, False, False, 0)
        spec_body.pack_start(label("AVAILABLE LIVE READINGS", "section"), False, False, 0)
        self.spec_grid = Gtk.Grid(column_spacing=14, row_spacing=6)
        self.spec_grid.set_margin_top(10)
        self.spec_grid.set_margin_bottom(8)
        spec_body.pack_start(self.spec_grid, False, False, 0)
        expander.add(spec_body)
        content.pack_start(expander, False, False, 0)
        self.unavailable_expander = Gtk.Expander(label="Unavailable readings")
        self.unavailable_expander.set_no_show_all(True)
        self.unavailable_readings = label("", "small", True)
        self.unavailable_readings.set_margin_top(8)
        self.unavailable_expander.add(self.unavailable_readings)
        content.pack_start(self.unavailable_expander, False, False, 0)
        processes = Gtk.Expander(label="GPU processes")
        self.process_text = label("Waiting for data", "small", True)
        self.process_text.set_margin_top(8)
        processes.add(self.process_text)
        content.pack_start(processes, False, False, 0)
        provenance = Gtk.Expander(label="Reading sources & availability")
        self.provenance = label("Waiting for data", "small", True)
        self.provenance.set_margin_top(8)
        provenance.add(self.provenance)
        content.pack_start(provenance, False, False, 0)

        footer = box(False)
        footer.set_margin_start(18)
        footer.set_margin_end(18)
        footer.set_margin_bottom(14)
        self.updated = label("First snapshot is on its way", "small")
        footer.pack_start(self.updated, True, True, 0)
        footer.pack_end(label("Drag header to move", "small"), False, False, 0)
        root.pack_end(footer, False, False, 0)

    def build_indicator(self):
        self.menu = Gtk.Menu()
        self.toggle_item = Gtk.MenuItem(label="Show GPU Snapshot")
        self.toggle_item.connect("activate", lambda _item: self.toggle_window())
        self.menu.append(self.toggle_item)
        self.tray_summary = Gtk.MenuItem(label="Reading GPU…")
        self.tray_summary.set_sensitive(False)
        self.menu.append(self.tray_summary)
        self.tray_models = Gtk.MenuItem(label="Checking models…")
        self.tray_models.set_sensitive(False)
        self.menu.append(self.tray_models)
        restart = Gtk.MenuItem(label="Reconnect if stopped")
        restart.connect("activate", lambda _item: self.start_monitor())
        self.menu.append(restart)
        self.menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit GPU Snapshot")
        quit_item.connect("activate", lambda _item: self.stop())
        self.menu.append(quit_item)
        self.menu.show_all()
        self.indicator = Indicator.Indicator.new("gputaskman-widget", "video-display-symbolic",
                                                 Indicator.IndicatorCategory.HARDWARE)
        self.indicator.set_title("GPU Snapshot")
        self.indicator.set_menu(self.menu)
        self.indicator.set_label("GPU …", "GPU 100% 100°C")
        self.indicator.set_secondary_activate_target(self.toggle_item)
        self.indicator.set_status(Indicator.IndicatorStatus.ACTIVE)

    def do_activate(self):
        if self.start_hidden:
            self.start_hidden = False
        else:
            self.show_window()

    def show_window(self):
        if not self.stopping:
            self.window.deiconify()
            self.window.show_all()
            self.window.present()
            self.toggle_item.set_label("Hide GPU Snapshot")

    def hide_window(self):
        self.window.hide()
        self.toggle_item.set_label("Show GPU Snapshot")

    def toggle_window(self):
        self.hide_window() if self.window.get_visible() else self.show_window()

    def on_drag(self, _widget, event):
        if event.button == 1:
            self.window.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
            return True
        return False

    def on_close(self, _window, _event):
        self.hide_window()
        return True

    def on_window_state(self, _window, event):
        if event.new_window_state & Gdk.WindowState.ICONIFIED:
            self.hide_window()
        return False

    def start_monitor(self):
        if self.worker is not None or self.stopping:
            return
        try:
            self.worker = Gio.Subprocess.new(self.command, Gio.SubprocessFlags.STDOUT_PIPE)
            self.reader = Gio.DataInputStream.new(self.worker.get_stdout_pipe())
            self.worker.wait_async(None, self.on_worker_exit)
            self.reader.read_line_async(GLib.PRIORITY_DEFAULT, None, self.on_line)
        except GLib.Error as error:
            self.worker = None
            self.set_unavailable(f"Could not start GPU reader: {error.message}")

    def on_line(self, reader, result):
        if self.stopping:
            return
        try:
            raw, _length = reader.read_line_finish(result)
            if raw is None:
                return
            snapshot = json.loads(raw)
            if snapshot.get("error"):
                self.set_unavailable(snapshot["error"])
            else:
                self.update_snapshot(snapshot)
        except (GLib.Error, ValueError, TypeError) as error:
            self.set_unavailable(f"Snapshot could not be read: {error}")
        if not self.stopping and reader is self.reader:
            reader.read_line_async(GLib.PRIORITY_DEFAULT, None, self.on_line)

    def on_worker_exit(self, worker, result):
        worker.wait_finish(result)
        if worker is self.worker:
            self.worker = None
        if self.stopping:
            if self.stop_timer:
                GLib.source_remove(self.stop_timer)
                self.stop_timer = None
            self.quit()
        else:
            self.set_unavailable("GPU reader stopped. Use Reconnect in the top-bar menu.")

    def update_snapshot(self, bundle):
        if "model_library" in bundle:
            names = bundle["model_library"]
            self.library_expander.set_label(f"Local model library · {len(names)} saved")
            self.library_text.set_text("\n".join(names) or "No model manifests found in the configured local folders.")
        snapshots = bundle.get("gpus") if "gpus" in bundle else [bundle]
        if not snapshots:
            self.set_unavailable(bundle.get("error") or "No GPU adapters detected")
            for row in self.gpu_rows.values():
                row[0].destroy()
            self.gpu_rows.clear()
            self.gpu_snapshots.clear()
            self.gpu_count.set_text("None detected")
            return
        self.last_update = time.monotonic()
        current = {}
        for snapshot in snapshots:
            identity = gpu_identity(snapshot)
            key = identity["id"]
            current[key] = snapshot
            history = self.histories.setdefault(key, deque(maxlen=60))
            used, total = snapshot.get("vram_used"), snapshot.get("vram_total")
            history.append(used / total * 100 if used is not None and total else None)
            if key not in self.gpu_rows:
                button = Gtk.Button()
                button.get_style_context().add_class("gpu-option")
                button.connect("clicked", lambda _button, gpu=key: self.select_gpu(gpu))
                row = box(False, 10)
                image = Gtk.Image.new_from_icon_name("video-display-symbolic", Gtk.IconSize.LARGE_TOOLBAR)
                image.get_style_context().add_class("metric-icon")
                row.pack_start(image, False, False, 0)
                names = box(spacing=3)
                heading = label("", "inventory-name")
                subtitle = label("", "small")
                names.pack_start(heading, False, False, 0)
                names.pack_start(subtitle, False, False, 0)
                row.pack_start(names, True, True, 0)
                reading = label("", "live")
                row.pack_end(reading, False, False, 0)
                button.add(row)
                self.gpu_list.pack_start(button, False, False, 0)
                self.gpu_rows[key] = button, heading, subtitle, reading
            button, heading, subtitle, reading = self.gpu_rows[key]
            heading.set_text(f"GPU {identity['index']}  ·  {identity['make']} {identity['model']}"
                             if not identity['model'].lower().startswith(identity['make'].lower())
                             else f"GPU {identity['index']}  ·  {identity['model']}")
            heading.set_ellipsize(Pango.EllipsizeMode.END)
            subtitle.set_text(f"{memory(total)} VRAM · {identity['board_make']}" + (f" · {identity['pci']}" if len(snapshots) > 1 else ""))
            nv = snapshot.get("device") or {}
            reading.set_text("OFFLINE" if snapshot.get("error") else metric(nv.get("temp"), "°C"))
            button.set_tooltip_text(f"Make: {identity['make']}\nModel: {identity['model']}\nBoard maker: {identity['board_make']}\nPCI: {identity['pci'] or 'not reported'}")
        for key in list(self.gpu_rows):
            if key not in current:
                self.gpu_rows.pop(key)[0].destroy()
                self.histories.pop(key, None)
        self.gpu_snapshots = current
        if self.selected_gpu not in current:
            self.selected_gpu = next(iter(current))
        self.gpu_count.set_text(f"{len(current)} detected")
        self.gpu_list.show_all()
        self.select_gpu(self.selected_gpu)
        if bundle.get("inventory_error"):
            self.notice.set_text(bundle["inventory_error"])
            self.notice.show()

    def select_gpu(self, key):
        if key not in self.gpu_snapshots:
            return
        self.selected_gpu = key
        for gpu_key, row in self.gpu_rows.items():
            style = row[0].get_style_context()
            style.add_class("selected-gpu") if gpu_key == key else style.remove_class("selected-gpu")
        self.history.values = self.histories[key]
        self.history.queue_draw()
        self.render_snapshot(self.gpu_snapshots[key])

    def render_snapshot(self, snapshot):
        self.last_snapshot = snapshot
        nv = snapshot.get("device") or {}
        identity_info = gpu_identity(snapshot)
        self.live.set_text("● LIVE")
        self.notice.set_text("")
        self.notice.hide()
        self.gpu_make.set_text(f"GPU {identity_info['index']}  /  {identity_info['make'].upper()}")
        self.gpu_name.set_text(identity_info["model"])
        self.board_name.set_text(f"Board by {identity_info['board_make']}" if identity_info['board_make'] != 'Not reported' else "Board maker not reported")
        product = identity_info["product"]
        if product:
            chip = (snapshot.get("hardware") or {}).get("model", "")
            self.board_name.set_text(" · ".join(x for x in (chip, product["board"]) if x))
        image_name = product.get("image")
        if image_name and image_name != self.current_product_image:
            image_path = Path(__file__).resolve().parents[1] / "assets/gpus" / image_name
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(image_path), 125, 90, True)
                self.product_image.set_from_pixbuf(pixbuf)
                self.product_image.set_tooltip_text("ASRock Intel Arc Pro B65 Creator product photograph")
                self.hero_icon.set_visible_child_name("product")
                self.current_product_image = image_name
            except GLib.Error:
                self.hero_icon.set_visible_child_name("generic")
        elif not image_name:
            self.hero_icon.set_visible_child_name("generic")
            self.current_product_image = None
        identity = [nv.get("pci_bus"), nv.get("driver_model"), snapshot.get("driver")]
        self.identity.set_text(" · ".join(x for x in identity if x))
        used, total = snapshot.get("vram_used"), snapshot.get("vram_total")
        self.vram_text.set_text(f"{memory(used)} / {memory(total)}")
        self.vram_pct.set_text(metric(used / total * 100, "%") if used is not None and total else "n/a")
        self.vram_bar.set_fraction(min(1, max(0, used / total)) if used is not None and total else 0)
        sources = nv.get("metric_sources") or {}
        note = snapshot.get("memory_note") or nv.get("memory_note") or ""
        source = sources.get("mem_used", nv.get("source") or "Device counter")
        self.memory_source.set_text(source)
        self.memory_source.set_tooltip_text(note or source)
        self.vram_text.set_tooltip_text(note or source)
        self.vram_bar.set_tooltip_text("Unknown — no memory reading" if used is None else note or source)
        formats = {"util_gpu": ("%", 0), "temp": ("°C", 0), "power_w": (" W", 0),
                   "sm_clock": (" MHz", 0), "fan_rpm": (" rpm", 0), "vram_temp": ("°C", 0)}
        for key, (value_label, caption, card) in self.metrics.items():
            value_label.set_text(metric(nv.get(key), *formats[key]))
            reason = (nv.get("metric_status") or {}).get(key)
            if nv.get(key) is None:
                low = (reason or "").lower()
                value_label.set_text("Locked" if "denied" in low or "privilege" in low else "Unknown")
            provenance = sources.get(key) or (nv.get("util_kind") if key == "util_gpu" else None)
            card.set_tooltip_text(reason if nv.get(key) is None else provenance or nv.get("source"))
            if key == "util_gpu":
                caption.set_text("GPU LOAD · EST." if nv.get("util_kind") else "GPU LOAD")
        cards = model_cards(snapshot)
        signature = json.dumps(cards, sort_keys=True)
        if signature != self.model_signature:
            self.model_signature = signature
            for child in self.models.get_children():
                child.destroy()
            if not cards:
                empty = box(False, 12, style="card")
                image = Gtk.Image.new_from_icon_name("applications-science-symbolic", Gtk.IconSize.LARGE_TOOLBAR)
                image.get_style_context().add_class("metric-icon")
                empty.pack_start(image, False, False, 0)
                message = box(spacing=4)
                message.pack_start(label("No active model detected", "inventory-name"), False, False, 0)
                message.pack_start(label("Visible processes on this GPU have no identified model.", "small", True), False, False, 0)
                empty.pack_start(message, True, True, 0)
                self.models.pack_start(empty, False, False, 0)
            for model in cards:
                card = box(style="card")
                card.get_style_context().add_class("model-card")
                heading = box(False, 8)
                heading.pack_start(label("AI MODEL", "eyebrow"), True, True, 0)
                heading.pack_end(label("PID " + str(model["pid"]), "badge"), False, False, 0)
                card.pack_start(heading, False, False, 0)
                card.pack_start(label(model["name"], "model-name", True), False, False, 0)
                card.pack_start(label(model['runtime'], "muted", True), False, False, 0)
                card.pack_start(label(model["evidence"], "live", True), False, False, 0)
                card.pack_start(label(model["details"], "small", True), False, False, 0)
                card.set_tooltip_text(model["files"] or model["evidence"])
                self.models.pack_start(card, False, False, 0)
            self.models.show_all()
        self.model_count.set_text(f"{len(cards)} process{'es' if len(cards) != 1 else ''}")
        self.update_specs(snapshot)
        procs = snapshot.get("processes") or []
        self.process_text.set_text("\n".join(f"{p.get('name', '?')} · PID {p.get('pid')} · {memory(p.get('vram_bytes'))} measured VRAM · {metric(p.get('gpu_pct'), '%', 1)} GPU" for p in procs) or "No visible GPU processes")
        notes = [snapshot.get("memory_note"), snapshot.get("counter_status"), snapshot.get("process_status")]
        notes.extend(f"{key}: {value}" for key, value in sources.items())
        _, unavailable = device_readings(snapshot)
        notes.extend(f"{key}: {value}" for key, value in unavailable.items())
        self.provenance.set_text("\n\n".join(x for x in notes if x) or "Device-reported counters")
        tray_parts = ["GPU" if len(self.gpu_snapshots) == 1 else f"GPU {identity_info['index']}"]
        if nv.get("util_gpu") is not None:
            tray_parts.append(metric(nv["util_gpu"], "%") + ("~" if nv.get("util_kind") else ""))
        if nv.get("temp") is not None:
            tray_parts.append(metric(nv["temp"], "°C"))
        self.indicator.set_label(" ".join(tray_parts), "GPU 100% 100°C")
        self.tray_summary.set_label(f"VRAM {memory(used)} / {memory(total)} · {metric(nv.get('power_w'), ' W')}")
        self.tray_models.set_label(cards[0]["name"][:85] if cards else "No active model identified")
        self.updated.set_text("Updated " + time.strftime("%H:%M:%S", time.localtime(snapshot.get("taken", time.time()))) + " · every 2 s")
        if snapshot.get("error"):
            self.set_unavailable(snapshot["error"])

    def update_specs(self, snapshot):
        rows, unavailable = device_readings(snapshot)
        identity = gpu_identity(snapshot)
        nv = snapshot.get("device") or {}
        values = {"GPU product": (identity["model"], "Product identity"),
                  "GPU make": (identity["make"], "PCI vendor"),
                  "Board maker": (identity["board_make"], "PCI subsystem vendor")}
        for name, value in (("PCI address", identity["pci"]), ("Kernel driver", nv.get("driver_model")),
                            ("Driver", snapshot.get("driver"))):
            if value:
                values[name] = (value, "Installed hardware/driver")
        values.update(rows)
        product = identity["product"]
        if product:
            self.product_specs.set_text("\n".join(f"{key}  ·  {value}" for key, value in product["specs"].items()))
            self.product_specs.set_tooltip_text("Source: " + product["source"])
            self.published_specs.show()
            for child in self.published_specs.get_children():
                child.show_all()
        else:
            self.published_specs.hide()
        for name, value_label in self.detail_values.items():
            value_label.set_text("n/a")
            value_label.hide()
            self.detail_keys[name].hide()
        for name, (value, source) in values.items():
            if name not in self.detail_values:
                row = len(self.detail_values)
                key_label = label(name, "small")
                key_label.set_no_show_all(True)
                key_label.set_size_request(160, -1)
                value_label = label("", "small", True)
                value_label.set_no_show_all(True)
                value_label.set_hexpand(True)
                self.spec_grid.attach(key_label, 0, row, 1, 1)
                self.spec_grid.attach(value_label, 1, row, 1, 1)
                self.detail_values[name] = value_label
                self.detail_keys[name] = key_label
            self.detail_values[name].set_text(value)
            self.detail_values[name].set_tooltip_text(source)
            self.detail_values[name].show()
            self.detail_keys[name].show()
        groups = {}
        for title, reason in unavailable.items():
            low = reason.lower()
            if "perf" in low and ("denied" in low or "privilege" in low):
                reason = "Protected performance counters. The GPU Sensors desktop helper can supply administrator readings."
            elif "pmt" in low and "denied" in low:
                reason = "Protected Intel PMT telemetry. The GPU Sensors desktop helper can supply administrator readings."
            elif "capability" in low or "configuration is truncated" in low:
                reason = "The current user cannot read the PCI configuration needed for live link status."
            groups.setdefault(reason, []).append(title)
        self.unavailable_readings.set_text("\n\n".join(
            ", ".join(titles) + "\n" + reason for reason, titles in groups.items()))
        self.unavailable_expander.set_label(f"Unavailable readings · {len(unavailable)} · why?")
        if unavailable:
            self.unavailable_expander.show()
            self.unavailable_readings.show()
        else:
            self.unavailable_expander.hide()

    def set_unavailable(self, reason):
        self.live.set_text("● UNAVAILABLE")
        suffix = "Last snapshot retained; readings are not live." if self.last_snapshot else "Waiting for the first reading."
        self.notice.set_text(reason + " " + suffix)
        self.notice.show()
        self.indicator.set_label("GPU offline", "GPU 100% 100°C")
        self.tray_summary.set_label("Readings unavailable — " + reason[:100])
        self.tray_models.set_label("Model information is not live")

    def check_freshness(self):
        if self.stopping:
            return GLib.SOURCE_REMOVE
        if self.last_update is not None and time.monotonic() - self.last_update > 8:
            self.set_unavailable("No fresh snapshot for " + str(int(time.monotonic() - self.last_update)) + " seconds.")
        elif self.last_update is None and time.monotonic() - self.started_at > 8:
            self.set_unavailable("GPU reader has not supplied a snapshot yet.")
        return GLib.SOURCE_CONTINUE

    def stop(self):
        if self.stopping:
            return GLib.SOURCE_REMOVE
        self.stopping = True
        self.hide_window()
        self.indicator.set_status(Indicator.IndicatorStatus.PASSIVE)
        if self.worker is None:
            self.quit()
        else:
            self.worker.send_signal(signal.SIGTERM)
            self.stop_timer = GLib.timeout_add_seconds(3, self.force_stop)
        return GLib.SOURCE_REMOVE

    def force_stop(self):
        self.stop_timer = None
        if self.worker:
            self.worker.force_exit()
        return GLib.SOURCE_REMOVE


def main():
    parser = argparse.ArgumentParser(description="Live GPU desktop snapshot widget")
    parser.add_argument("--start-hidden", action="store_true")
    parser.add_argument("--python", default=sys.executable, help="Python with gputaskman dependencies")
    parser.add_argument("--gpu", type=int, help="show only this adapter (default: all)")
    args = parser.parse_args()
    if args.gpu is not None and args.gpu < 0:
        parser.error("GPU index must be non-negative")
    source = str(Path(__file__).resolve().parents[1] / "src")
    os.environ["PYTHONPATH"] = source + os.pathsep + os.environ.get("PYTHONPATH", "")
    command = [args.python, "-m", "gputaskman.widget"]
    if args.gpu is not None:
        command.extend(["--gpu", str(args.gpu)])
    return GpuWidget(command, start_hidden=args.start_hidden).run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
