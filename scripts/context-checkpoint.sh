#!/usr/bin/env bash
# context-checkpoint.sh — Save a milestone during work
# Usage: bash scripts/context-checkpoint.sh "what was accomplished"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OB_ROOT="$(dirname "$SCRIPT_DIR")"
MILESTONE="${1:-checkpoint}"

# Planning content (including CONTEXT_CHECKPOINTS.md) moved to the sibling
# repo in the 2026-04-22 carve-out. Default target is the sibling dir on
# disk; override with PLAN_DIR if cloned elsewhere. If the sibling repo
# isn't cloned, fall back to the legacy path under open-brain/docs/planning
# so a checkpoint is never silently lost — but warn loudly.
PLAN_DIR="${PLAN_DIR:-$(dirname "$OB_ROOT")/open-brain-planning}"
if [ -d "$PLAN_DIR" ]; then
    CHECKPOINTS_FILE="$PLAN_DIR/CONTEXT_CHECKPOINTS.md"
else
    CHECKPOINTS_FILE="$OB_ROOT/docs/planning/CONTEXT_CHECKPOINTS.md"
    echo "  WARN: planning sibling repo not found at $PLAN_DIR" >&2
    echo "  WARN: falling back to legacy path (not tracked after carve-out): $CHECKPOINTS_FILE" >&2
    echo "  WARN: clone degailen/open-brain-planning to stop losing checkpoints." >&2
fi

mkdir -p "$(dirname "$CHECKPOINTS_FILE")"

TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
BRANCH=$(git -C "$OB_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "n/a")

# Append to checkpoints file
cat >> "$CHECKPOINTS_FILE" <<EOF

## $TIMESTAMP
- **Branch:** $BRANCH
- **Milestone:** $MILESTONE
EOF

echo "  Checkpoint saved: $MILESTONE"
echo "  File: $CHECKPOINTS_FILE"
