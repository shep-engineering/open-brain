#!/usr/bin/env bash
# Open Brain OFF — cross-platform shutdown script
# Supports: macOS, Linux, WSL
# Usage: bash open-brain-off.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OB_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Open Brain OFF..."

# ── Step 1: Stop MCP server ───────────────────────────────────────────────────
echo "[1/2] Stopping Open Brain MCP server..."
if command -v tmux >/dev/null 2>&1; then
    if tmux has-session -t openbrain 2>/dev/null; then
        tmux kill-session -t openbrain
        echo "      tmux session 'openbrain' stopped."
    else
        echo "      No tmux session found."
    fi
fi

# Also check for background PID file (non-tmux fallback)
PID_FILE="$OB_ROOT/.open-brain-sh.pid"
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "      Background process $PID stopped."
    fi
    rm -f "$PID_FILE"
fi

# ── Step 2: Stop postgres container ──────────────────────────────────────────
echo "[2/2] Stopping open-brain-db (Docker)..."
if docker stop open-brain-db >/dev/null 2>&1; then
    echo "      open-brain-db stopped."
else
    echo "      open-brain-db was not running."
fi

echo ""
echo "Open Brain is OFF."
