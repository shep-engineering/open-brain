#!/usr/bin/env bash
# branch-guard.sh — PreToolUse hook.
# Blocks git commit on protected branches (main, master, develop).
# Push is allowed on main (needed for feature branch → merge → push workflow).
#
# Repos can opt out by placing a .no-branch-guard file at their root.
#
# Install: copy to ~/.claude/hooks/ and add to settings.json PreToolUse
# (matcher: "Bash"). See settings.snippet.json.

set -euo pipefail
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('command',''))" 2>/dev/null || echo "")

if echo "$COMMAND" | grep -qE 'git commit'; then
    REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
    if [ -f "$REPO_ROOT/.no-branch-guard" ]; then
        exit 0
    fi
    BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    if echo "$BRANCH" | grep -qE '^(main|master|develop)$'; then
        echo "<hook-warning>"
        echo "BRANCH GUARD: You are on branch '$BRANCH'."
        echo "Never commit directly to main/master/develop."
        echo "Create a feature branch: git checkout -b feat/description"
        echo "Test fully, merge to main, then push."
        echo "</hook-warning>"
        exit 2
    fi
fi
exit 0
