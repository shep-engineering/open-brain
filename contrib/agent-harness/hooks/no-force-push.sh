#!/usr/bin/env bash
# no-force-push.sh — PreToolUse hook.
# Blocks git push --force and git push -f without explicit user confirmation.
# Suggests --force-with-lease as a safer alternative.
#
# Install: copy to ~/.claude/hooks/ and add to settings.json PreToolUse
# (matcher: "Bash"). See settings.snippet.json.

set -euo pipefail
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('command',''))" 2>/dev/null || echo "")

if echo "$COMMAND" | grep -qE 'git push.*(--force|-f)\b'; then
    echo "<hook-warning>"
    echo "FORCE PUSH GUARD: git push --force detected."
    echo "This is a destructive operation. Get explicit user confirmation before proceeding."
    echo "Prefer: git push --force-with-lease (fails if remote has diverged unexpectedly)"
    echo "</hook-warning>"
    exit 2
fi
exit 0
