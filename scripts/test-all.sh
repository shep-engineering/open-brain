#!/usr/bin/env bash
# Run the full test suite with parallelization where safe.
# V1 parallel: 4 workers, loadfile distribution (excludes serial-marked tests)
# V1 serial: heartbeat agent + any other serial-marked tests
# V2: serial (real Ollama, isolated test DB on port 5435)
set -e

PYTEST=".venv/Scripts/python -m pytest"

echo "=== V1 tests: parallel (4 workers, excludes @serial) ==="
$PYTEST tests/ -n 4 --dist loadfile -m "not serial" -v --tb=short

echo ""
echo "=== V1 tests: serial (@serial marked) ==="
$PYTEST tests/ -m "serial" -v --tb=short

echo ""
echo "=== V2 tests: serial (test DB port 5435) ==="
$PYTEST brain_v2/tests/ -v --tb=short

echo ""
echo "=== ALL DONE ==="
