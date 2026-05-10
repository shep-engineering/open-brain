#!/usr/bin/env bash
# require-prework.sh — PreToolUse hook for Open Brain.
#
# Blocks Bash, Edit, and Write calls if pre-work-check.sh has not been run
# or if it recorded a failure. Reads the "status: pass|fail" field written
# by scripts/pre-work-check.sh into the most recent .task-markers/*-start.txt
# in the current git repo.
#
# Only enforces in repos that have a .task-markers/ directory (opt-in).
# This keeps the hook inert in repos that don't use the task-marker workflow.
#
# Always allows:
#   - pre-work-check.sh itself (circular-block escape)
#   - post-work-check.sh (let the done gate run)
#   - Brain startup scripts (must run before any marker exists)
#
# To bypass legitimately: ask the user explicitly for permission first.
#
# Install: copy to ~/.claude/hooks/ and add to settings.json PreToolUse
# (matcher: "Bash" and "Edit|Write"). See settings.snippet.json.

INPUT=$(cat)
_py() { python -c "$1" 2>/dev/null || python3 -c "$1" 2>/dev/null; }

TOOL_NAME=$(echo "$INPUT" | _py "import sys,json; print(json.load(sys.stdin).get('tool_name',''))")
TOOL_CMD=$(echo  "$INPUT" | _py "import sys,json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('command',''))")

# Only gate Bash, Edit, Write
if ! echo "$TOOL_NAME" | grep -qE "^(Bash|Edit|Write)$"; then
  exit 0
fi

# Allow: pre/post-work-check.sh itself (avoid circular block)
if echo "$TOOL_CMD" | grep -qE "(pre|post)-work-check\.sh"; then
  exit 0
fi

# Allow: brain startup scripts (must run even before any marker exists)
if echo "$TOOL_CMD" | grep -qE "open-brain-on\.(sh|cmd)|brain-v2-up\.(sh|cmd)|ensure-stack\.sh|docker.*(open-brain|brain-v2|postgres)"; then
  exit 0
fi

# Find the current git repo root
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [ -z "$REPO_ROOT" ]; then
  exit 0  # Not in a git repo — no enforcement
fi

MARKERS_DIR="$REPO_ROOT/.task-markers"
if [ ! -d "$MARKERS_DIR" ]; then
  exit 0  # Repo doesn't use task-markers — no enforcement
fi

# ── deny helper ───────────────────────────────────────────────────────────────
deny() {
  local reason="$1"
  python3 -c "
import json, sys
print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',
    'permissionDecision': 'deny', 'permissionDecisionReason': sys.argv[1]}}))
" "$reason" 2>/dev/null || \
  python -c "
import json, sys
print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',
    'permissionDecision': 'deny', 'permissionDecisionReason': sys.argv[1]}}))
" "$reason"
  exit 0
}

# ── Find most recent start marker ────────────────────────────────────────────
LATEST_START=$(ls -t "$MARKERS_DIR"/*-start.txt 2>/dev/null | head -1)

if [ -z "$LATEST_START" ]; then
  deny "PRE-WORK GATE: pre-work-check.sh has never been run in this repo.
Run: bash scripts/pre-work-check.sh 'task description'
ALL checks must pass. To bypass: ask the user explicitly for permission."
fi

# ── Read pass/fail status ─────────────────────────────────────────────────────
STATUS=$(grep "^status:" "$LATEST_START" 2>/dev/null | tail -1 | awk '{print $2}')

if [ "$STATUS" = "pass" ]; then
  exit 0
fi

TASK=$(grep "^task:" "$LATEST_START" 2>/dev/null | head -1 | sed 's/^task:[[:space:]]*//')

if [ -z "$STATUS" ]; then
  # Marker predates v0.24.4 (no status field) — warn but don't block
  echo "[require-prework] WARN: marker $(basename "$LATEST_START") has no status field. Re-run pre-work-check.sh to get a pass marker." >&2
  exit 0
fi

deny "PRE-WORK GATE: Last pre-work-check FAILED.
Task: $TASK
Re-run: bash scripts/pre-work-check.sh 'task description'
Fix ALL failures before proceeding.
To bypass: ask the user explicitly for permission."
