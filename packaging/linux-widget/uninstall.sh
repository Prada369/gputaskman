#!/bin/sh
set -eu

DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
INSTALL_ROOT="$DATA_HOME/gputaskman-widget"
BIN_FILE="$HOME/.local/bin/gputaskman-widget"
DESKTOP_FILE="$DATA_HOME/applications/gputaskman-widget.desktop"

rm -f "$BIN_FILE" "$DESKTOP_FILE"
rm -rf "$INSTALL_ROOT"
echo "Removed the gputaskman widget application. Preferences were left untouched."
