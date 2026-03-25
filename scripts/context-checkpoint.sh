#!/usr/bin/env bash
# context-checkpoint.sh — Save a milestone during work
# Usage: bash scripts/context-checkpoint.sh "what was accomplished"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OB_ROOT="$(dirname "$SCRIPT_DIR")"
MILESTONE="${1:-checkpoint}"
CHECKPOINTS_FILE="$OB_ROOT/docs/planning/CONTEXT_CHECKPOINTS.md"

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
