#!/usr/bin/env bash
# Open Brain ON — cross-platform startup script
# Supports: macOS, Linux, WSL
# Usage: bash open-brain-on.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OB_ROOT="$(dirname "$SCRIPT_DIR")"

# Detect platform
detect_platform() {
    case "$(uname -s)" in
        Darwin) echo "mac" ;;
        Linux)
            if grep -qi microsoft /proc/version 2>/dev/null; then
                echo "wsl"
            else
                echo "linux"
            fi
            ;;
        *) echo "unknown" ;;
    esac
}

PLATFORM=$(detect_platform)
echo "Open Brain ON — platform: $PLATFORM"

# ── Step 1: Ensure Docker is running ─────────────────────────────────────────
echo "[1/4] Checking Docker..."
if ! docker info >/dev/null 2>&1; then
    case "$PLATFORM" in
        mac)
            echo "      Starting Docker Desktop (macOS)..."
            open -a Docker
            echo "      Waiting for Docker to start..."
            for i in $(seq 1 15); do
                sleep 2
                docker info >/dev/null 2>&1 && break
            done
            ;;
        linux)
            echo "      Starting Docker daemon (Linux)..."
            sudo systemctl start docker 2>/dev/null || sudo service docker start 2>/dev/null || true
            sleep 3
            ;;
        wsl)
            echo "      WSL: Docker Desktop should be running on Windows host."
            echo "      Please start Docker Desktop on Windows and re-run this script."
            exit 1
            ;;
    esac
    if docker info >/dev/null 2>&1; then
        echo "      Docker ready."
    else
        echo "      ERROR: Docker did not start. Please start it manually."
        exit 1
    fi
else
    echo "      Docker already running."
fi

# ── Step 2: Start postgres container ─────────────────────────────────────────
echo "[2/4] Checking open-brain-db (Docker)..."
if docker start open-brain-db >/dev/null 2>&1; then
    echo "      open-brain-db OK"
else
    echo "      open-brain-db not found — starting via docker compose..."
    docker compose -f "$OB_ROOT/docker-compose.yml" up -d
    echo "      open-brain-db started."
fi

# ── Step 3: Ensure Ollama is running ─────────────────────────────────────────
echo "[3/4] Checking Ollama..."
if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "      Ollama already running."
else
    echo "      Starting Ollama..."
    case "$PLATFORM" in
        mac)
            open -a Ollama 2>/dev/null || ollama serve &>/dev/null &
            ;;
        linux|wsl)
            ollama serve &>/dev/null &
            ;;
    esac
    echo "      Ollama started."
fi

# ── Step 4: Start Open Brain MCP server ──────────────────────────────────────
echo "[4/4] Starting Open Brain MCP server..."

START_SH="$OB_ROOT/start_server.sh"

if command -v tmux >/dev/null 2>&1; then
    tmux kill-session -t openbrain 2>/dev/null || true
    tmux new-session -d -s openbrain "bash '$START_SH'"
    echo "      Open Brain server started (tmux: openbrain)"
else
    # No tmux — run detached, save PID
    bash "$START_SH" &>/dev/null &
    echo $! > "$OB_ROOT/.open-brain-sh.pid"
    echo "      Open Brain server started (PID: $!, saved to .open-brain-sh.pid)"
fi

echo ""
echo "Open Brain is ON."
echo "  MCP server:  stdio via tmux session 'openbrain' (or background PID)"
echo "  REST health: http://localhost:8765/health"
echo ""
