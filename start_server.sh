#!/bin/bash
# Auto-detects WSL vs native Linux and calls the correct Python binary.
# If using a Windows .exe, converts paths to Windows format via wslpath.
# Logs crashes to server-crash.log for diagnosis.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRASH_LOG="$SCRIPT_DIR/server-crash.log"

WINDOWS_PYTHON="$SCRIPT_DIR/.venv/Scripts/python.exe"
LINUX_PYTHON="$SCRIPT_DIR/.venv/bin/python"

_run() {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] server.py starting (pid=$$)" >> "$CRASH_LOG"
    "$@" 2>> "$CRASH_LOG"
    EXIT_CODE=$?
    if [ $EXIT_CODE -ne 0 ]; then
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] server.py exited with code $EXIT_CODE" >> "$CRASH_LOG"
    fi
    exit $EXIT_CODE
}

if [ -f "$LINUX_PYTHON" ]; then
    # Native Linux venv — pass paths as-is
    _run "$LINUX_PYTHON" "$SCRIPT_DIR/server.py" "$@"
elif [ -f "$WINDOWS_PYTHON" ]; then
    # Windows venv running under WSL — convert paths to Windows format
    WIN_SERVER="$(wslpath -w "$SCRIPT_DIR/server.py")"
    _run "$WINDOWS_PYTHON" "$WIN_SERVER" "$@"
else
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ERROR: Could not find python in $SCRIPT_DIR/.venv" >> "$CRASH_LOG"
    echo "ERROR: Could not find python in $SCRIPT_DIR/.venv" >&2
    exit 1
fi
