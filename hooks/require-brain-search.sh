#!/usr/bin/env bash
# require-brain-search.sh — PreToolUse hook for Open Brain
# Blocks non-brain tool calls until mcp__open-brain__search has been called.
# Reads the session transcript to verify a search has happened.
# Install: copy to ~/.claude/hooks/ and add to ~/.claude/settings.json

set -e

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | python -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null || echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null)
TRANSCRIPT_PATH=$(echo "$INPUT" | python -c "import sys,json; print(json.load(sys.stdin).get('transcript_path',''))" 2>/dev/null || echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('transcript_path',''))" 2>/dev/null)

# Always allow brain tools themselves
if echo "$TOOL_NAME" | grep -q "^mcp__open-brain__"; then
  exit 0
fi

# Always allow read-only exploration tools (needed for session startup)
if echo "$TOOL_NAME" | grep -qE "^(Read|Glob|Grep|ToolSearch)$"; then
  exit 0
fi

# Check if brain search has been called in this session
if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
  if grep -q "mcp__open-brain__search" "$TRANSCRIPT_PATH" 2>/dev/null; then
    # Brain has been searched — allow everything
    exit 0
  fi
fi

# Brain not searched yet — block with explanation
cat <<'EOF'
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"BLOCKED: You must search Open Brain before using other tools. Call mcp__open-brain__search with your task topic FIRST. This is a mandatory workflow rule."}}
EOF
exit 0
