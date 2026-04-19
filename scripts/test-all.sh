#!/usr/bin/env bash
# Run the full test suite with parallelization.
# V1: 4 workers, loadfile distribution (all tests including heartbeat)
# V2: serial (real Ollama, isolated test DB on port 5435)
set -e

PYTEST=".venv/Scripts/python -m pytest"

echo "=== V1 tests: parallel (4 workers) ==="
$PYTEST tests/ -n 4 --dist loadfile -v --tb=short

echo ""
echo "=== V2 tests: serial (test DB port 5435) ==="
$PYTEST brain_v2/tests/ -v --tb=short

echo ""
echo "=== ALL DONE ==="
