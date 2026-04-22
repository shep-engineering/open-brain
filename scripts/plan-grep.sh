#!/usr/bin/env bash
# plan-grep.sh — Search the sibling planning repo.
#
# Open-brain's planning docs live in degailen/open-brain-planning (private),
# cloned as a sibling directory at ../open-brain-planning on disk. The public
# mirror of open-brain deliberately omits those docs, so we can't just grep
# ./docs/planning/ anymore.
#
# Usage:
#   bash scripts/plan-grep.sh "registry trust"
#   bash scripts/plan-grep.sh -l "contributor"       # pass additional grep flags through
#
# Env override:
#   PLAN_DIR=/alt/path/open-brain-planning bash scripts/plan-grep.sh ...
#
# Fails loudly with a clone hint if the sibling repo is not present.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

PLAN_DIR="${PLAN_DIR:-$(dirname "$REPO_ROOT")/open-brain-planning}"

if [ ! -d "$PLAN_DIR" ]; then
    echo "ERROR: planning repo not found at: $PLAN_DIR" >&2
    echo "" >&2
    echo "Clone it alongside open-brain on disk:" >&2
    echo "  git clone git@github-degailen:degailen/open-brain-planning.git \\" >&2
    echo "    \"$PLAN_DIR\"" >&2
    echo "" >&2
    echo "Or set PLAN_DIR to an alternate location." >&2
    exit 1
fi

if [ $# -lt 1 ]; then
    echo "Usage: $(basename "$0") [grep flags] <pattern>" >&2
    echo "Example: $(basename "$0") 'session registry'" >&2
    exit 2
fi

# Recursive, case-insensitive by default; line numbers; skip .git.
grep -rniI --exclude-dir=.git "$@" "$PLAN_DIR"
