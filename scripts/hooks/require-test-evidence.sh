#!/usr/bin/env bash
# Blocks git commit unless a test-evidence file exists and is < 30 minutes old.
# The "if: Bash(git commit*)" filter in settings.json ensures this only runs
# for git commit commands — no need to re-parse stdin here.

EVIDENCE="F:/open-brain/.task-markers/test-evidence.txt"
MAX_AGE_SECONDS=1800  # 30 minutes

if [ ! -f "$EVIDENCE" ]; then
  printf '{"decision":"block","reason":"BLOCKED: No functional test evidence found. Run open-brain-on.cmd and open-brain-off.cmd end-to-end, then write proof to .task-markers/test-evidence.txt before committing."}'
  exit 2
fi

# Use Python for portable stat (works on Windows/Git-bash where stat -r differs)
AGE=$(python3 -c "import os,time; print(int(time.time()-os.path.getmtime('$EVIDENCE')))" 2>/dev/null || echo 99999)

if [ "$AGE" -gt "$MAX_AGE_SECONDS" ]; then
  printf "{\"decision\":\"block\",\"reason\":\"BLOCKED: Test evidence is stale (${AGE}s old, max ${MAX_AGE_SECONDS}s). Re-run the scripts end-to-end and update .task-markers/test-evidence.txt.\"}"
  exit 2
fi

exit 0
