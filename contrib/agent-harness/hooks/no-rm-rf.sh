#!/usr/bin/env bash
# no-rm-rf.sh — PreToolUse hook.
# Blocks rm -rf and rm --recursive without explicit user confirmation.
# Suggests moving to a backup location first.
#
# Install: copy to ~/.claude/hooks/ and add to settings.json PreToolUse
# (matcher: "Bash"). See settings.snippet.json.

set -euo pipefail
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('command',''))" 2>/dev/null || echo "")

if echo "$COMMAND" | grep -qE 'rm\s+-[^\s]*r[^\s]*f|rm\s+--recursive'; then
    echo "<hook-warning>"
    echo "RM -RF GUARD: Recursive delete detected: $COMMAND"
    echo "This is irreversible. Get explicit user confirmation before running."
    echo "Prefer: move to a backup location first, then delete after confirming."
    echo "</hook-warning>"
    exit 2
fi
exit 0
