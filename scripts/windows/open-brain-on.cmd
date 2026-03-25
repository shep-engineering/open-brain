@echo off
title Open Brain ON
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
if %errorlevel%==0 (echo     open-brain-db OK) else (echo     open-brain-db FAILED - is Docker running?)

echo [3/4] Checking Ollama...
curl -sf http://localhost:11434/api/tags >nul 2>&1
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

echo [4/4] Starting Open Brain MCP server in WSL tmux...
wsl -e bash -lc "tmux kill-session -t openbrain 2>/dev/null; tmux new -d -s openbrain 'F:/open-brain/.venv/Scripts/python.exe /mnt/f/open-brain/server.py'" >nul 2>&1
if %errorlevel%==0 (echo     Open Brain server started ^(tmux: openbrain^)) else (
    echo     WSL tmux failed - trying Windows venv directly...
    start "" /min F:\open-brain\.venv\Scripts\python.exe F:\open-brain\server.py
    echo     Open Brain server started ^(Windows venv^)
)

echo.
echo Open Brain v0.4.1 is ON. 12 MCP tools ready for Windsurf / Cursor / Claude Code.
echo   - Hybrid search ^(vector + full-text^), uptime-based decay, time-scoped search
echo   - procedural + episodic memory types, pinned guardrails
timeout /t 3 >nul
