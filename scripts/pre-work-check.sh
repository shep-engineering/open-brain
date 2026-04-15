#!/usr/bin/env bash
# pre-work-check.sh — Run before starting ANY task
# Enforces: Open Brain reachable, feature branch, task marker written
# Usage: bash scripts/pre-work-check.sh "task description"

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OB_ROOT="$(dirname "$SCRIPT_DIR")"
TASK="${1:-unspecified task}"
MARKERS_DIR="$OB_ROOT/.task-markers"
mkdir -p "$MARKERS_DIR"

PASS=0
FAIL=0

check() {
    local label="$1"
    local result="$2"
    if [[ "$result" == "ok" ]]; then
        echo "  [PASS] $label"
        PASS=$((PASS + 1))
    else
        echo "  [FAIL] $label"
        echo "         --> ${result#fail: }"
        FAIL=$((FAIL + 1))
    fi
}

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║         PRE-WORK CHECK                   ║"
echo "╚══════════════════════════════════════════╝"
echo "  Task: $TASK"
echo ""

# ── 1. Open Brain reachable ───────────────────────────────────────────────────
if curl -sf http://localhost:8765/health >/dev/null 2>&1; then
    check "Open Brain reachable (localhost:8765)" "ok"
else
    check "Open Brain reachable (localhost:8765)" "fail: Open Brain is not running. Start it with 'Open Brain ON' on Desktop (Windows) or: bash scripts/open-brain-on.sh (WSL/Linux/Mac)"
fi

# ── 2. Not on a protected branch ─────────────────────────────────────────────
if git -C "$OB_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    BRANCH=$(git -C "$OB_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    if [[ "$BRANCH" =~ ^(main|master|develop)$ ]]; then
        check "Feature branch (current: $BRANCH)" "fail: You are on '$BRANCH'. Create a feature branch: git checkout -b feat/your-task"
    else
        check "Feature branch (current: $BRANCH)" "ok"
    fi
else
    check "Feature branch" "ok (not a git repo — skipped)"
fi

# ── 3. Write task start marker ────────────────────────────────────────────────
MARKER="$MARKERS_DIR/$(date +%Y%m%d-%H%M%S)-start.txt"
echo "task: $TASK" > "$MARKER"
echo "started: $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$MARKER"
echo "branch: $(git -C "$OB_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'n/a')" >> "$MARKER"
check "Task marker written ($MARKER)" "ok"

# ── Result ────────────────────────────────────────────────────────────────────
echo ""
if [[ $FAIL -gt 0 ]]; then
    echo "  PRE-WORK FAILED ($FAIL issue(s)). Resolve above before proceeding."
    echo ""
    exit 1
else
    echo "  PRE-WORK PASSED. You may begin."
    echo ""
    exit 0
fi
