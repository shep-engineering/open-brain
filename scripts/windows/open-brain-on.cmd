@echo off
mkdir F:\open-brain\logs 2>nul
echo Starting Open Brain... > F:\open-brain\logs\startup.log

echo [1/4] Starting Docker Desktop (if needed)... >> F:\open-brain\logs\startup.log
docker info >nul 2>&1
if %errorlevel% neq 0 (
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe" >nul 2>&1
    echo     Waiting for Docker to start... >> F:\open-brain\logs\startup.log
    timeout /t 10 >nul
    docker info >nul 2>&1
    if %errorlevel%==0 (echo     Docker ready >> F:\open-brain\logs\startup.log) else (echo     Docker not responding >> F:\open-brain\logs\startup.log)
) else (
    echo     Docker already running >> F:\open-brain\logs\startup.log
)

echo [2/4] Checking open-brain-db >> F:\open-brain\logs\startup.log
docker start open-brain-db >nul 2>&1
if %errorlevel%==0 (echo     open-brain-db OK >> F:\open-brain\logs\startup.log) else (echo     open-brain-db FAILED >> F:\open-brain\logs\startup.log)

echo [3/4] Checking Ollama... >> F:\open-brain\logs\startup.log
curl -sf http://localhost:11434/api/tags >nul 2>&1
if %errorlevel%==0 (
    echo     Ollama already running >> F:\open-brain\logs\startup.log
) else (
    echo     Starting Ollama >> F:\open-brain\logs\startup.log
    set OLLAMA_NUM_GPU=2
    set CUDA_VISIBLE_DEVICES=0,1
    set OLLAMA_KEEP_ALIVE=30m
    set OLLAMA_MAX_LOADED_MODELS=2
    start "" /B ollama serve >F:\open-brain\logs\ollama.log 2>&1
    echo     Ollama started >> F:\open-brain\logs\startup.log
)

echo [4/4] Starting Open Brain MCP server >> F:\open-brain\logs\startup.log
wsl -e bash -lc "tmux kill-session -t openbrain 2>/dev/null; tmux new -d -s openbrain 'F:/open-brain/.venv/Scripts/python.exe /mnt/f/open-brain/server.py'" >nul 2>&1
if %errorlevel%==0 (echo     MCP server started >> F:\open-brain\logs\startup.log) else (
    echo     WSL tmux failed - using Windows venv >> F:\open-brain\logs\startup.log
    start "" /B F:\open-brain\.venv\Scripts\python.exe F:\open-brain\server.py
)
echo Open Brain ON >> F:\open-brain\logs\startup.log
