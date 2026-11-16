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

## Source and status

This is a community side project, not part of the upstream TUI release. Source and updates:
[Prada369/gputaskman, `fix/intel-xe-telemetry`](https://github.com/Prada369/gputaskman/tree/sideproject/linux-widget-package/packaging/linux-widget).
The widget has been exercised on Ubuntu with an Intel Arc Pro B65; other GPUs and desktop sessions
may expose different readings or tray behavior.
