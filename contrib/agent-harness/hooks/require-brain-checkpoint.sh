#!/usr/bin/env bash
# require-brain-checkpoint.sh — PreToolUse hook.
# Blocks edits to infrastructure, database, deployment, and configuration
# files unless a brain_checkpoint call has been made in this session first.
# This ensures the agent has surfaced existing implementations, guardrails,
# and prior decisions before modifying risky files.
#
# Customize RISKY_PATTERNS for your project's file categories.
#
# Install: copy to ~/.claude/hooks/ and add to settings.json PreToolUse
# (matcher: "Edit|Write"). See settings.snippet.json.

set -e

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | python -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null \
         || python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" <<< "$INPUT" 2>/dev/null)
TOOL_INPUT=$(echo "$INPUT" | python -c "import sys,json; d=json.load(sys.stdin).get('tool_input',{}); print(d.get('file_path','') or d.get('command','') or d.get('path','') or '')" 2>/dev/null || echo "")
TRANSCRIPT_PATH=$(echo "$INPUT" | python -c "import sys,json; print(json.load(sys.stdin).get('transcript_path',''))" 2>/dev/null || echo "")

# Only check Edit and Write tools
if [ "$TOOL_NAME" != "Edit" ] && [ "$TOOL_NAME" != "Write" ]; then
  exit 0
fi

# ── Customize: risky file categories for your project ────────────────────────
IS_RISKY=false
CATEGORY=""

case "$TOOL_INPUT" in
  *docker-compose*|*Dockerfile*|*.env*|*deploy*|*nginx*|*fly.toml*)
    IS_RISKY=true; CATEGORY="deployment" ;;
  *scripts/*|*\.cmd|*\.sh|*\.ps1)
    IS_RISKY=true; CATEGORY="infrastructure" ;;
  *migration*|*alembic*|*setup_db*|*schema*)
    IS_RISKY=true; CATEGORY="database" ;;
  *server.py|*app.py|*main.py|*api.py)
    IS_RISKY=true; CATEGORY="server" ;;
  *settings.json|*hooks/*|*CLAUDE.md|*AGENTS.md)
    IS_RISKY=true; CATEGORY="configuration" ;;
esac

if [ "$IS_RISKY" != "true" ]; then
  exit 0
fi

# Check if brain_checkpoint was called in this session
if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
  if grep -qE "mcp__open-brain__brain_checkpoint|mcp__open-brain-v2__brain_checkpoint_v2" "$TRANSCRIPT_PATH" 2>/dev/null; then
    exit 0
  fi
fi

cat <<EOF
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"BLOCKED: Call mcp__open-brain__brain_checkpoint or mcp__open-brain-v2__brain_checkpoint_v2 BEFORE editing $CATEGORY files. This surfaces existing implementations, guardrails, and prior decisions. File: $TOOL_INPUT"}}
EOF
exit 0
