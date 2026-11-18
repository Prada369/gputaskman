# gputaskman Linux desktop widget (Ubuntu bundle)

An unofficial, optional GTK desktop companion for gputaskman. It runs separately from the
cross-platform terminal UI and does not change the upstream app's scope. This bundle is built
from the `sideproject/linux-widget-package` fork branch and includes the widget and its Python backend.

## Requirements

- Ubuntu 24.04 or newer with a supported GPU driver.
- System Python with GTK 3 introspection (`python3-gi`, `gir1.2-gtk-3.0`).
- Python 3.11 or newer with `venv` and `pip` (`python3-venv` on Ubuntu).
- Internet access during the first install to fetch the Python dependencies.

Ayatana AppIndicator support is optional. Install `gir1.2-ayatanaappindicator3-0.1` and use a
desktop session that displays application indicators to get the top-panel menu. Without it, the
widget is a normal taskbar window and can still be minimized and restored there.

## Install

Install the system pieces once:

```sh
sudo apt update
sudo apt install python3-gi gir1.2-gtk-3.0 python3-venv
```

For an AppIndicator menu on the top panel, also install:

```sh
sudo apt install gir1.2-ayatanaappindicator3-0.1
```

Extract this archive, open a terminal in its `gputaskman-widget` folder, and run:

```sh
./install.sh
```

The installer puts the application under `~/.local/share/gputaskman-widget`, adds
`~/.local/bin/gputaskman-widget`, and creates a **GPU Taskman Widget** application-menu entry.
It does not use `sudo`, install a driver, change permissions, install a fan helper, or start a
model or inference workload. The first install downloads the declared Python dependencies.

## Run and remove

Launch **GPU Taskman Widget** from the application menu or run:

```sh
gputaskman-widget
```

Pass `--gpu N` to show one adapter. To remove the app and launcher, run `./uninstall.sh` from the
extracted bundle. Your widget preferences in `~/.config/gputaskman/widget.json` are kept.

The widget refreshes the same local telemetry as gputaskman. Unsupported or protected readings
remain unavailable with a reason. Fan and power controls only work where the active driver and
existing system authorization support them; this bundle does not install privileged helpers or
change system policy.

### Optional Intel Xe VRAM temperature fan target

On Xe drivers exposing both VRAM temperature sensors and writable `pwm1`, the fan dialog offers
an 80°C Performance target and an adjustable Custom VRAM target (65–95°C). The controller samples
VRAM channels and GPU package temperature every two seconds, raises fan duty as temperatures
approach the target, and restores the prior fan mode when stopped or when the widget exits. This is
a best-effort userspace control, not a hard temperature limit. It requires no reboot or driver
reload; install the helper once, then restart the widget:

```sh
sudo install -o root -g root -m 0755 tools/gputaskman-vram-fan-target /usr/local/bin/gputaskman-vram-fan-target
```

Applying a target prompts for administrator authorization. Closing the widget stops the live
controller and restores its previous fan mode. If VRAM sensor readings become unavailable, the
controller stops and attempts to restore that mode.

The **Stop temperature control** button restores the previous curve. In Custom VRAM target mode,
the temperature and minimum-duty controls also update the running controller without another
authorization prompt. The controller exits if the widget disconnects or stops responding.

### Widget v0.2.0

The widget version is shown beneath the window title, in the top-panel menu, and by
`gputaskman-widget --version`. It is separate from the backend's version.

For brief Xe tachometer zero reports, the top fan block retains the last non-zero sample for up
to eight seconds and visibly labels its age. The raw zero remains in the sensor details and
alarm evaluation. This avoids the brief display dropout while preserving the driver's reading;
it does not repair the driver's tachometer. The clock block explains why an idle GPU may report
0 MHz despite a configured active minimum.

## Source and status

### Widget v2.1.0

**Power & clocks** now includes Performance, Quiet, Efficiency and a saved custom profile per GPU.
Loading or saving a preset only edits local fields. **Start temporary profile** requests authorization
once and applies the selected clocks, power profile, power cap and VRAM-target fan control. Stop,
widget exit, lost snapshots, or a disconnected control pipe restore the entry settings.

Workload mode activates when reported GPU load or a visible process's GPU engine load reaches 5%.
After 15 seconds without that evidence, it restores the entry settings and remains armed. Model
residency alone never activates it. Protected or unavailable activity counters can prevent activation.

The optional thermal power guard lowers the session cap by 5 W every four seconds if VRAM exceeds
the target by more than 1°C while fan duty is essentially full. It stays above your chosen minimum.
After cooling 5°C below target it cautiously restores power, at most 5 W every twenty seconds,
up to the selected session cap. This remains best effort, not a guaranteed temperature limit.

The dialog shows driver throttle reasons and recent device telemetry averages. **Save comparison
snapshot** writes bounded telemetry, settings and source labels to
`~/.local/state/gputaskman/comparisons/`. It starts no workload and measures no model throughput.
The v2.1.0 update uses the already-loaded driver and requires only a widget relaunch. Synthetic
tests cover session restoration and guard behavior; the new profile/guard path has not been
stress-tested on the owner's B65.

This is a community side project, not part of the upstream TUI release. Source and updates:
[Prada369/gputaskman, `sideproject/linux-widget-package`](https://github.com/Prada369/gputaskman/tree/sideproject/linux-widget-package/packaging/linux-widget).
The widget has been exercised on Ubuntu with an Intel Arc Pro B65; other GPUs and desktop sessions
may expose different readings or tray behavior.
