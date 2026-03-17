#!/bin/bash
# Auto-detects WSL vs native Linux and calls the correct Python binary.
# If using a Windows .exe, converts paths to Windows format via wslpath.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WINDOWS_PYTHON="$SCRIPT_DIR/.venv/Scripts/python.exe"
LINUX_PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [ -f "$LINUX_PYTHON" ]; then
    # Native Linux venv — pass paths as-is
    exec "$LINUX_PYTHON" "$SCRIPT_DIR/server.py" "$@"
elif [ -f "$WINDOWS_PYTHON" ]; then
    # Windows venv running under WSL — convert paths to Windows format
    WIN_PYTHON="$(wslpath -w "$WINDOWS_PYTHON")"
    WIN_SERVER="$(wslpath -w "$SCRIPT_DIR/server.py")"
    exec "$WINDOWS_PYTHON" "$WIN_SERVER" "$@"
else
    echo "ERROR: Could not find python in $SCRIPT_DIR/.venv" >&2
    exit 1
fi
