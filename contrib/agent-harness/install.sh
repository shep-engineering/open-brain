#!/usr/bin/env bash
# install.sh — Install the Open Brain agent harness hooks.
# Works on Linux, macOS, and WSL.
#
# Usage:
#   bash contrib/agent-harness/install.sh             # install all hooks
#   bash contrib/agent-harness/install.sh --tier1     # safety guards only
#   bash contrib/agent-harness/install.sh --dry-run   # preview without writing
#
# What it does:
#   1. Copies hook files to ~/.claude/hooks/
#   2. Makes them executable
#   3. Prints the settings.json snippet with the correct paths substituted
#
# After running, manually merge the printed snippet into ~/.claude/settings.json.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_SRC="$SCRIPT_DIR/hooks"
HOOKS_DEST="$HOME/.claude/hooks"
DRY_RUN=0
TIER="all"

for arg in "$@"; do
  case "$arg" in
    --dry-run)   DRY_RUN=1 ;;
    --tier1)     TIER="tier1" ;;
    --all)       TIER="all" ;;
  esac
done

# ── Hook tiers ────────────────────────────────────────────────────────────────
# Tier 1: safety guards — no brain dependency, universal value
TIER1_HOOKS=(
  branch-guard.sh
  no-force-push.sh
  no-rm-rf.sh
)

# Tier 2 (default --all): brain integration hooks
TIER2_HOOKS=(
  require-brain-boot.sh
  require-prework.sh
  require-brain-save.sh
  require-brain-checkpoint.sh
  detect-correction.sh
  session-end-save.py
)

if [ "$TIER" = "tier1" ]; then
  HOOKS=("${TIER1_HOOKS[@]}")
else
  HOOKS=("${TIER1_HOOKS[@]}" "${TIER2_HOOKS[@]}")
fi

# ── Install ───────────────────────────────────────────────────────────────────
echo ""
echo "Open Brain Agent Harness — installer"
echo "Destination: $HOOKS_DEST"
echo "Tier: $TIER (${#HOOKS[@]} hooks)"
[ "$DRY_RUN" = "1" ] && echo "[DRY RUN — no files will be written]"
echo ""

if [ "$DRY_RUN" = "0" ]; then
  mkdir -p "$HOOKS_DEST"
fi

for hook in "${HOOKS[@]}"; do
  src="$HOOKS_SRC/$hook"
  dst="$HOOKS_DEST/$hook"
  if [ ! -f "$src" ]; then
    echo "  [SKIP] $hook (not found in $HOOKS_SRC)"
    continue
  fi
  if [ "$DRY_RUN" = "1" ]; then
    echo "  [DRY]  $hook  →  $dst"
  else
    cp "$src" "$dst"
    chmod +x "$dst"
    echo "  [DONE] $hook  →  $dst"
  fi
done

# ── Print settings.json snippet ───────────────────────────────────────────────
echo ""
echo "=========================================================="
echo " Next step: merge this into ~/.claude/settings.json"
echo "=========================================================="
echo ""
sed "s|HOOKS_DIR|$HOOKS_DEST|g" "$SCRIPT_DIR/settings.snippet.json"
echo ""
echo "If settings.json already has a 'hooks' key, merge the blocks"
echo "manually — don't replace the whole file."
echo ""

# ── Reminder for .task-markers ────────────────────────────────────────────────
echo "NOTE: require-prework.sh only enforces in repos that have a"
echo ".task-markers/ directory. To opt in a repo:"
echo "  mkdir -p /path/to/your/repo/.task-markers"
echo "  echo '.task-markers/' >> /path/to/your/repo/.gitignore"
echo ""
echo "Then run scripts/pre-work-check.sh before starting work in that repo."
