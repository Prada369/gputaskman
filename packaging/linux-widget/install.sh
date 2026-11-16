#!/bin/sh
set -eu

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
CONFIG_HOME=${XDG_CONFIG_HOME:-"$HOME/.config"}
INSTALL_ROOT="$DATA_HOME/gputaskman-widget"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$DATA_HOME/applications"
VERSION=$(cat "$SOURCE_DIR/VERSION")
SYSTEM_PYTHON=${SYSTEM_PYTHON:-/usr/bin/python3}

if [ ! -x "$SYSTEM_PYTHON" ]; then
    echo "System Python not found at $SYSTEM_PYTHON" >&2
    exit 1
fi

if ! "$SYSTEM_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    echo "Python 3.11 or newer is required. Install python3.11+ and set SYSTEM_PYTHON if needed." >&2
    exit 1
fi

if ! "$SYSTEM_PYTHON" -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' 2>/dev/null; then
    echo "GTK 3 Python bindings are missing. Install: sudo apt install python3-gi gir1.2-gtk-3.0" >&2
    exit 1
fi

if ! "$SYSTEM_PYTHON" -m venv --help >/dev/null 2>&1; then
    echo "Python venv support is missing. On Ubuntu install: sudo apt install python3-venv" >&2
    exit 1
fi

mkdir -p "$INSTALL_ROOT" "$BIN_DIR" "$APP_DIR" "$CONFIG_HOME/gputaskman"
VENV="$INSTALL_ROOT/venv"
if [ ! -x "$VENV/bin/python" ]; then
    "$SYSTEM_PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --disable-pip-version-check --upgrade -r "$SOURCE_DIR/requirements.txt"

mkdir -p "$INSTALL_ROOT/src" "$INSTALL_ROOT/tools" "$INSTALL_ROOT/assets/gpus"
rm -rf "$INSTALL_ROOT/src/gputaskman"
cp -a "$SOURCE_DIR/src/gputaskman" "$INSTALL_ROOT/src/"
cp "$SOURCE_DIR/tools/desktop_widget.py" "$INSTALL_ROOT/tools/desktop_widget.py"
cp -a "$SOURCE_DIR/assets/gpus/." "$INSTALL_ROOT/assets/gpus/"
cp "$SOURCE_DIR/LICENSE" "$INSTALL_ROOT/LICENSE"
cp "$SOURCE_DIR/VERSION" "$INSTALL_ROOT/VERSION"

cat > "$BIN_DIR/gputaskman-widget" <<EOF
#!/bin/sh
exec "$SYSTEM_PYTHON" "$INSTALL_ROOT/tools/desktop_widget.py" --python "$VENV/bin/python" "\$@"
EOF
chmod 755 "$BIN_DIR/gputaskman-widget"

cat > "$APP_DIR/gputaskman-widget.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=GPU Taskman Widget
Comment=Live GPU desktop monitor
Exec="$SYSTEM_PYTHON" "$INSTALL_ROOT/tools/desktop_widget.py" --python "$VENV/bin/python"
Icon=video-display-symbolic
Terminal=false
Categories=System;Monitor;
StartupNotify=false
EOF

echo "Installed gputaskman widget $VERSION. Run: $BIN_DIR/gputaskman-widget"
echo "Preferences remain in $CONFIG_HOME/gputaskman/widget.json."
