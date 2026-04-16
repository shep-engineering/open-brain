#!/usr/bin/env bash
# Starts the open-brain-v2-db Postgres container on port 5433.
# Does NOT start the MCP server (that's spawned per-client via mcp_config).
# Does NOT touch v1's open-brain-db container on port 5432.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OB_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Starting Open Brain v2 database container..."
docker compose -f "$OB_ROOT/docker-compose.v2.yml" up -d

echo "Waiting for health check..."
sleep 5

if docker exec open-brain-v2-db pg_isready -U postgres -d open_brain_v2 >/dev/null 2>&1; then
    echo "Open Brain v2 database is HEALTHY."
    echo "  Container: open-brain-v2-db"
    echo "  Port: 5433"
    echo "  Database: open_brain_v2"
else
    echo "WARNING: Container started but health check failed. Give it a few more seconds."
fi

echo ""
echo "v2 MCP server will start automatically when an agent connects via mcp_config."
