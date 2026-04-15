#!/usr/bin/env bash
# post-work-check.sh — Run before declaring ANY task complete
# Enforces: tests were run, test results documented, nothing skipped
# Usage: bash scripts/post-work-check.sh "what was tested"
#
# AGENTS: You MUST run this before saying "done". If you skip it,
# you are handing untested work to the user. That is not acceptable.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OB_ROOT="$(dirname "$SCRIPT_DIR")"
TEST_SUMMARY="${1:-}"
MARKERS_DIR="$OB_ROOT/.task-markers"
mkdir -p "$MARKERS_DIR"

PASS=0
FAIL=0

check() {
    local label="$1"
    local result="$2"
    if [[ "$result" == "ok" ]]; then
        echo "  [PASS] $label"
        PASS=$((PASS + 1))
    else
        echo "  [FAIL] $label"
        echo "         --> ${result#fail: }"
        FAIL=$((FAIL + 1))
    fi
}

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║         POST-WORK CHECK                  ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── 1. Test summary must be provided ─────────────────────────────────────────
if [[ -z "$TEST_SUMMARY" ]]; then
    check "Test summary provided" "fail: You must pass a test summary as argument: bash scripts/post-work-check.sh \"what I tested and what passed\""
else
    check "Test summary provided" "ok"
    echo "     Summary: $TEST_SUMMARY"
fi

# ── 2. A start marker must exist (pre-work was run) ──────────────────────────
START_MARKERS=("$MARKERS_DIR"/*-start.txt)
if [[ -f "${START_MARKERS[0]}" ]]; then
    LATEST_START="${START_MARKERS[-1]}"
    check "Pre-work-check was run (marker: $(basename "$LATEST_START"))" "ok"
else
    check "Pre-work-check was run" "fail: No task start marker found. Did you run pre-work-check.sh before starting?"
fi

# ── 3. No uncommitted secrets (basic check) ──────────────────────────────────
if git -C "$OB_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if git -C "$OB_ROOT" diff --cached --name-only 2>/dev/null | xargs grep -l -E "(api_key|secret|password|token)\s*=" 2>/dev/null | grep -qv ".example"; then
        check "No secrets in staged files" "fail: Possible secrets detected in staged files. Review before committing."
    else
        check "No secrets in staged files" "ok"
    fi
else
    check "No secrets check" "ok (not a git repo — skipped)"
fi

# ── 4. Write completion marker ────────────────────────────────────────────────
MARKER="$MARKERS_DIR/$(date +%Y%m%d-%H%M%S)-done.txt"
echo "completed: $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$MARKER"
echo "tested: $TEST_SUMMARY" >> "$MARKER"
check "Completion marker written ($(basename "$MARKER"))" "ok"

# ── Result ────────────────────────────────────────────────────────────────────
echo ""
if [[ $FAIL -gt 0 ]]; then
    echo "  POST-WORK FAILED ($FAIL issue(s))."
    echo "  Do NOT tell the user the task is done until all checks pass."
    echo ""
    exit 1
else
    echo "  POST-WORK PASSED. You may now hand off to the user."
    echo ""
    exit 0
fi
