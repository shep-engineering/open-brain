#!/usr/bin/env bash
# Run the full test suite.
# V1: serial (shared singleton state prevents parallelization)
# V2: serial (real Ollama, isolated test DB on port 5435)
# Both suites use isolated test databases — never touches production.
set -e

PYTEST=".venv/Scripts/python -m pytest"

echo "=== V1 tests (185 tests, test DB port 5434) ==="
$PYTEST tests/ -v --tb=short

echo ""
echo "=== V2 tests (203 tests, test DB port 5435) ==="
$PYTEST brain_v2/tests/ -v --tb=short

echo ""
echo "=== ALL DONE ==="
