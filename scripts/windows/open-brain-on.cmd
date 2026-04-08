@echo off
SETLOCAL EnableDelayedExpansion
title Open Brain ON
mkdir F:\open-brain\logs 2>nul
echo Starting Open Brain MCP server...

echo [1/4] Starting Docker Desktop (if needed)...
docker info >nul 2>&1
if %errorlevel% neq 0 (
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe" >nul 2>&1
    echo     Waiting for Docker to start...
    timeout /t 10 >nul
    docker info >nul 2>&1
    if %errorlevel%==0 (echo     Docker ready) else (echo     Docker not responding - open Docker Desktop manually)
) else (
    echo     Docker already running
)

echo [2/4] Checking open-brain-db (Docker)...
docker start open-brain-db >nul 2>&1
if %errorlevel%==0 (echo     open-brain-db container OK) else (echo     open-brain-db FAILED - is Docker running?)

echo     Waiting for PostgreSQL to accept connections...
set PG_READY=0
for /L %%i in (1,1,15) do (
    if !PG_READY!==0 (
        F:\open-brain\.venv\Scripts\python.exe -c "import psycopg2; psycopg2.connect('postgresql://postgres:password@127.0.0.1:5432/openbrain', connect_timeout=2).close(); print('ready')" >nul 2>&1
        if !errorlevel!==0 (
            set PG_READY=1
            echo     PostgreSQL ready
        ) else (
            timeout /t 2 >nul
        )
    )
)
if !PG_READY!==0 (
    echo     WARNING: PostgreSQL not responding after 30s - server may fail to connect
)

echo [3/4] Checking Ollama...
curl -sf http://127.0.0.1:11434/api/tags >nul 2>&1
if %errorlevel%==0 (
    echo     Ollama already running
) else (
    echo     Starting Ollama ^(dual GPU^)...
    set OLLAMA_NUM_GPU=2
    set CUDA_VISIBLE_DEVICES=0,1
    set OLLAMA_KEEP_ALIVE=30m
    set OLLAMA_MAX_LOADED_MODELS=2
    start "" /B ollama serve >F:\open-brain\logs\ollama.log 2>&1
    echo     Ollama started ^(dual GPU, max 2 models loaded^)
)

echo [4/4] Starting Open Brain MCP server...
start "" /B F:\open-brain\.venv\Scripts\python.exe F:\open-brain\server.py 2>>F:\open-brain\server-crash.log
echo     Open Brain server started

echo.
echo Open Brain v0.4.1 is ON. 12 MCP tools ready for Windsurf / Cursor / Claude Code.
echo   - Hybrid search ^(vector + full-text^), uptime-based decay, time-scoped search
echo   - procedural + episodic memory types, pinned guardrails
