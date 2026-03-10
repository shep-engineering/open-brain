#!/bin/bash
# =============================================================================
# ensure-stack.sh — Verify the AI stack is running; start it if not.
# =============================================================================
# Checks: Ollama (port 11434) + open-brain-db Docker container
# If either is down, launches "AI Mode ON.cmd" and waits for readiness.
#
# Usage:
#   bash F:/open-brain/scripts/ensure-stack.sh           # check + auto-start
#   bash F:/open-brain/scripts/ensure-stack.sh --check   # check only, no start
# =============================================================================

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

OLLAMA_URL="http://localhost:11434"
AI_MODE_CMD='C:\Users\DAVE\Desktop\AI Mode ON.cmd'
OLLAMA_WAIT_SECS=60
PG_WAIT_SECS=40

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

check_ollama() {
    curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1
}

check_postgres() {
    docker ps --filter "name=open-brain-db" --filter "status=running" \
        --format "{{.Names}}" 2>/dev/null | grep -q "open-brain-db"
}

stack_ready() {
    check_ollama && check_postgres
}

if stack_ready; then
    echo "${GREEN}✅ AI stack is running (Ollama + open-brain-db)${NC}"
    exit 0
fi

# Report what's missing
check_ollama   || echo "${YELLOW}⚠️  Ollama not reachable at $OLLAMA_URL${NC}"
check_postgres || echo "${YELLOW}⚠️  open-brain-db container not running${NC}"

if [ "$CHECK_ONLY" = "1" ]; then
    echo "${YELLOW}ℹ️  Run 'AI Mode ON.cmd' to start the stack.${NC}"
    exit 1
fi

echo "${YELLOW}⚡ Starting AI stack via AI Mode ON.cmd...${NC}"
cmd.exe /c "$AI_MODE_CMD" 2>/dev/null &

# Wait for Ollama
echo "   Waiting for Ollama (up to ${OLLAMA_WAIT_SECS}s)..."
for i in $(seq 1 $((OLLAMA_WAIT_SECS / 2))); do
    sleep 2
    if check_ollama; then
        echo "${GREEN}  ✅ Ollama is ready${NC}"
        break
    fi
done

if ! check_ollama; then
    echo "${RED}❌ Ollama did not start within ${OLLAMA_WAIT_SECS}s${NC}"
    echo "   Open 'AI Mode ON.cmd' manually from the Desktop."
    exit 1
fi

# Wait for PostgreSQL
echo "   Waiting for open-brain-db (up to ${PG_WAIT_SECS}s)..."
for i in $(seq 1 $((PG_WAIT_SECS / 2))); do
    sleep 2
    if check_postgres; then
        echo "${GREEN}  ✅ open-brain-db is ready${NC}"
        break
    fi
done

if ! check_postgres; then
    echo "${RED}❌ open-brain-db did not start within ${PG_WAIT_SECS}s${NC}"
    echo "   Docker Desktop may still be loading — wait 30s and retry."
    exit 1
fi

echo ""
echo "${GREEN}✅ AI stack is ready. MCP tools (open-brain) are available.${NC}"
