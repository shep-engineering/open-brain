#!/usr/bin/env bash
# require-brain-boot.sh — PreToolUse hook for Open Brain.
# Cross-platform: Windows (Git Bash), WSL, macOS, Linux.
#
# Blocks non-brain, non-read tools until boot_session has been called for
# BOTH V1 (mcp__open-brain__boot_session) and V2
# (mcp__open-brain-v2__boot_session_v2) and both returned success.
#
# DEADLOCK ESCAPE: If the Open Brain MCP server is down, brain tools won't
# appear in the tool list. The hook whitelists brain startup script patterns
# so the server can be started without already needing the brain running.
# PowerShell is always allowed through (Windows escape hatch).
#
# Install: copy to ~/.claude/hooks/ and add to settings.json PreToolUse
# (matcher: "(?!mcp__open-brain).*"). See settings.snippet.json.

INPUT=$(cat)

_py() {
  python -c "$1" 2>/dev/null || python3 -c "$1" 2>/dev/null
}

TOOL_NAME=$(echo "$INPUT"    | _py "import sys,json; print(json.load(sys.stdin).get('tool_name',''))")
TRANSCRIPT_PATH=$(echo "$INPUT" | _py "import sys,json; print(json.load(sys.stdin).get('transcript_path',''))")
TOOL_CMD=$(echo "$INPUT"     | _py "import sys,json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('command',''))")

# ── Always allow: brain MCP tools (V1 and V2) ────────────────────────────────
if echo "$TOOL_NAME" | grep -qE "^mcp__open-brain(|-v2)__"; then
  exit 0
fi

# ── Always allow: read-only discovery tools ───────────────────────────────────
if echo "$TOOL_NAME" | grep -qE "^(Read|Glob|Grep|ToolSearch|Agent)$"; then
  exit 0
fi

# ── Always allow: PowerShell ──────────────────────────────────────────────────
if [ "$TOOL_NAME" = "PowerShell" ]; then
  exit 0
fi

# ── Deadlock escape: brain startup commands ───────────────────────────────────
if [ "$TOOL_NAME" = "Bash" ]; then
  if echo "$TOOL_CMD" | grep -qE \
    "open-brain-on\.(sh|cmd)|brain-v2-up\.(sh|cmd)|ensure-stack\.sh|docker.*(open-brain|brain-v2|postgres|pgvector)"; then
    exit 0
  fi
fi

deny() {
  local reason="$1"
  python -c "
import json, sys
print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',
    'permissionDecision': 'deny', 'permissionDecisionReason': sys.argv[1]}}))
" "$reason" 2>/dev/null \
  || python3 -c "
import json, sys
print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',
    'permissionDecision': 'deny', 'permissionDecisionReason': sys.argv[1]}}))
" "$reason"
  exit 0
}

if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
  deny "BLOCKED: no transcript found. Call mcp__open-brain__boot_session(project, source='claude') AND mcp__open-brain-v2__boot_session_v2(project, task, source='claude') BEFORE any other tool."
fi

# ── Classify boot state from transcript ──────────────────────────────────────
STATE=$(python - "$TRANSCRIPT_PATH" 2>/dev/null || python3 - "$TRANSCRIPT_PATH" <<'PY'
import json, sys

path = sys.argv[1]
V1 = "mcp__open-brain__boot_session"
V2 = "mcp__open-brain-v2__boot_session_v2"

def classify(text):
    if not text:
        return ("degraded", "empty result")
    try:
        inner = json.loads(text)
        if isinstance(inner, dict) and "result" in inner and isinstance(inner["result"], str):
            try:
                inner = json.loads(inner["result"])
            except Exception:
                pass
    except Exception:
        inner = None
    if isinstance(inner, dict):
        if inner.get("success") is False:
            return ("degraded", f"success=false: {inner.get('error','')[:120]}")
        if inner.get("degraded") is True:
            return ("degraded", f"degraded=true: {inner.get('error','')[:120]}")
        if inner.get("blocked_by"):
            return ("degraded", f"blocked_by: {inner.get('blocked_by')}")
    lowered = text.lower()
    if "connection refused" in lowered or "could not connect" in lowered:
        return ("degraded", "connection refused")
    return ("ok", "")

pairs = {V1: [], V2: []}
try:
    with open(path, "r", encoding="utf-8") as f:
        pending = {}
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            msg = obj.get("message") or {}
            content = msg.get("content") or []
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    if block.get("name") in (V1, V2):
                        pending[block.get("id")] = block.get("name")
                elif block.get("type") == "tool_result":
                    tid = block.get("tool_use_id")
                    name = pending.pop(tid, None)
                    if name not in (V1, V2):
                        continue
                    inner = block.get("content")
                    if isinstance(inner, list):
                        txt = "".join(s.get("text","") for s in inner if isinstance(s,dict) and s.get("type")=="text")
                        inner = txt
                    elif not isinstance(inner, str):
                        inner = json.dumps(inner)
                    pairs[name].append(inner)
except Exception as e:
    print(f"missing:read-error:{e}"); sys.exit(0)

r1 = pairs[V1][-1] if pairs[V1] else None
r2 = pairs[V2][-1] if pairs[V2] else None

if r1 is None and r2 is None:
    print("missing:both"); sys.exit(0)
if r1 is None:
    print("missing_v1"); sys.exit(0)
if r2 is None:
    print("missing_v2"); sys.exit(0)

s1, n1 = classify(r1)
s2, n2 = classify(r2)

if s1 == "degraded":
    print(f"degraded_v1:{n1}"); sys.exit(0)
if s2 == "degraded":
    print(f"degraded_v2:{n2}"); sys.exit(0)

print("ok")
PY
)

STARTUP_HINT="To start: scripts/windows/open-brain-on.cmd (Windows) or bash scripts/open-brain-on.sh (Linux/macOS/WSL). Wait 10-15s then retry boot_session."

case "$STATE" in
  ok)
    exit 0 ;;
  missing*)
    deny "BLOCKED: boot_session not called yet. Call mcp__open-brain__boot_session(project, source='claude') AND mcp__open-brain-v2__boot_session_v2(project, task, source='claude') before any other tool. $STARTUP_HINT" ;;
  degraded_v1:*)
    deny "BLOCKED: V1 boot_session degraded — ${STATE#degraded_v1:}. Do NOT proceed. $STARTUP_HINT" ;;
  degraded_v2:*)
    deny "BLOCKED: V2 boot_session_v2 degraded — ${STATE#degraded_v2:}. Do NOT proceed. $STARTUP_HINT" ;;
  *)
    echo "[require-brain-boot] unexpected state: $STATE" >&2
    exit 0 ;;
esac
