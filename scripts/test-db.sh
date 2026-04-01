#!/bin/bash
# Start test database container and run pytest.
# Usage: bash scripts/test-db.sh [pytest args...]
#
# Examples:
#   bash scripts/test-db.sh              # run all tests
#   bash scripts/test-db.sh -v           # verbose
#   bash scripts/test-db.sh -k pinned    # only pinned memory tests
#   bash scripts/test-db.sh -m ollama    # only tests needing real Ollama

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "Starting test database..."
docker compose -f docker-compose.test.yml up -d --wait

echo "Test DB ready on port 5434"
echo "Running tests..."
.venv/Scripts/python.exe -m pytest tests/ "$@"
