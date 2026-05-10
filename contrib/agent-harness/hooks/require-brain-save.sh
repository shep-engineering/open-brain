#!/usr/bin/env bash
# require-brain-save.sh — PreToolUse hook.
# Blocks git commit if code changes were made in this session but nothing
# was written to Open Brain. Ensures lessons learned, decisions, and context
# are captured before committing.
#
# Install: copy to ~/.claude/hooks/ and add to settings.json PreToolUse
# (matcher: "Bash"). See settings.snippet.json.

set -e

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | python -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null \
         || python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" <<< "$INPUT" 2>/dev/null)
TOOL_INPUT=$(echo "$INPUT" | python -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null \
           || python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" <<< "$INPUT" 2>/dev/null)
TRANSCRIPT_PATH=$(echo "$INPUT" | python -c "import sys,json; print(json.load(sys.stdin).get('transcript_path',''))" 2>/dev/null \
                || python3 -c "import sys,json; print(json.load(sys.stdin).get('transcript_path',''))" <<< "$INPUT" 2>/dev/null)

# Only trigger on Bash tool calls that contain "git commit"
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi
if ! echo "$TOOL_INPUT" | grep -q "git commit"; then
  exit 0
fi

# Check if brain was written to in this session (V1 or V2)
if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
  if grep -qE "mcp__open-brain__(remember|capture_context)|mcp__open-brain-v2__(remember_rule_v2|remember_fact_v2|remember_incident_v2|remember_task_v2|capture_context_v2)" "$TRANSCRIPT_PATH" 2>/dev/null; then
    exit 0
  fi
fi

cat <<'EOF'
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"BLOCKED: You have code changes but haven't saved anything to Open Brain this session. Before committing, call mcp__open-brain__capture_context or mcp__open-brain-v2__capture_context_v2 with: what was changed, why, lessons learned, and any decisions made. This ensures future sessions have context for this work."}}
EOF
exit 0
