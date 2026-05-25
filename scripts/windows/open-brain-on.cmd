@echo off
SETLOCAL EnableDelayedExpansion
title Open Brain ON

REM Resolve the repo root relative to this script's location, so the
REM launcher works regardless of where the repo is installed.
set SCRIPT_DIR=%~dp0
set OB_ROOT=%SCRIPT_DIR%..\..
for %%I in ("%OB_ROOT%") do set OB_ROOT=%%~fI

set PYTHON=%OB_ROOT%\.venv\Scripts\python.exe
set DB_URL=postgresql://postgres:password@127.0.0.1:5432/openbrain

mkdir "%OB_ROOT%\logs" 2>nul
echo Starting Open Brain MCP server...

echo [1/6] Starting Docker Desktop (if needed)...
docker info >nul 2>&1
if %errorlevel% neq 0 (
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe" >nul 2>&1
    echo     Waiting for Docker to start...
    set DOCKER_READY=0
    for /L %%i in (1,1,15) do (
        if !DOCKER_READY!==0 (
            docker info >nul 2>&1
            if !errorlevel!==0 (
                set DOCKER_READY=1
                echo     Docker ready
            ) else (
                timeout /t 2 >nul
            )
        )
    )
    if !DOCKER_READY!==0 (echo     Docker not responding after 30s - open Docker Desktop manually)
) else (
    echo     Docker already running
)

echo [2/6] Checking open-brain-db and open-brain-v2-db (Docker)...
docker start open-brain-db >nul 2>&1
if %errorlevel%==0 (echo     open-brain-db container OK) else (echo     open-brain-db FAILED - is Docker running?)
docker start open-brain-v2-db >nul 2>&1
if %errorlevel%==0 (echo     open-brain-v2-db container OK) else (echo     open-brain-v2-db FAILED - is Docker running?)

echo     Waiting for PostgreSQL to accept connections...
set PG_READY=0
for /L %%i in (1,1,15) do (
    if !PG_READY!==0 (
        "%PYTHON%" -c "import psycopg2; psycopg2.connect('%DB_URL%', connect_timeout=2).close(); print('ready')" >nul 2>&1
        if !errorlevel!==0 (
            set PG_READY=1
            echo     open-brain-db PostgreSQL ready
        ) else (
            timeout /t 2 >nul
        )
    )
)
if !PG_READY!==0 (
    echo     WARNING: open-brain-db PostgreSQL not responding after 30s - server may fail to connect
)
set DB_URL_V2=postgresql://postgres:password@127.0.0.1:5433/open_brain_v2
set PG_V2_READY=0
for /L %%i in (1,1,15) do (
    if !PG_V2_READY!==0 (
        "%PYTHON%" -c "import psycopg2; psycopg2.connect('%DB_URL_V2%', connect_timeout=2).close(); print('ready')" >nul 2>&1
        if !errorlevel!==0 (
            set PG_V2_READY=1
            echo     open-brain-v2-db PostgreSQL ready
        ) else (
            timeout /t 2 >nul
        )
    )
)
if !PG_V2_READY!==0 (
    echo     WARNING: open-brain-v2-db PostgreSQL not responding after 30s - server may fail to connect
)

echo [3/6] Checking Ollama...
curl -sf http://127.0.0.1:11434/api/tags >nul 2>&1
if %errorlevel%==0 (
    echo     Ollama already running
) else (
    echo     Starting Ollama ^(dual GPU, LAN-exposed on 0.0.0.0:11434^)...
    set OLLAMA_HOST=0.0.0.0:11434
    set OLLAMA_NUM_GPU=2
    set CUDA_VISIBLE_DEVICES=0,1
    set OLLAMA_KEEP_ALIVE=30m
    set OLLAMA_MAX_LOADED_MODELS=2
    start "" /B ollama serve >"%OB_ROOT%\logs\ollama.log" 2>&1
    echo     Ollama started ^(dual GPU, max 2 models loaded, bound 0.0.0.0:11434^)
)

echo [4/6] Starting Open Brain MCP servers (HTTP transport)...
REM Kill any stale server processes before starting fresh
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":8080 "') do taskkill /PID %%p /F >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":8081 "') do taskkill /PID %%p /F >nul 2>&1

start "" /B cmd /c ""%PYTHON%" "%OB_ROOT%\server.py" --transport http --port 8080 2>>"%OB_ROOT%\logs\server-v1-crash.log""
start "" /B cmd /c ""%PYTHON%" "%OB_ROOT%\brain_v2\server.py" --transport http --port 8081 2>>"%OB_ROOT%\logs\server-v2-crash.log""

echo     Waiting for HTTP servers to accept connections...
set V1_READY=0
set V2_READY=0
for /L %%i in (1,1,15) do (
    if !V1_READY!==0 (
        curl -s --max-time 2 http://127.0.0.1:8080/mcp >nul 2>&1
        if !errorlevel! neq 7 (set V1_READY=1 & echo     open-brain v1 HTTP ready ^(port 8080^))
    )
    if !V2_READY!==0 (
        curl -s --max-time 2 http://127.0.0.1:8081/mcp >nul 2>&1
        if !errorlevel! neq 7 (set V2_READY=1 & echo     open-brain v2 HTTP ready ^(port 8081^))
    )
    if !V1_READY!==1 if !V2_READY!==1 goto :servers_ready
    timeout /t 2 >nul
)
:servers_ready
if !V1_READY!==0 (echo     WARNING: open-brain v1 not responding on port 8080 - check logs\server-v1-crash.log)
if !V2_READY!==0 (echo     WARNING: open-brain v2 not responding on port 8081 - check logs\server-v2-crash.log)

echo [5/6] Starting session-registry heartbeat agent (v0.14.0+)...
schtasks /query /tn OpenBrainHeartbeatAgent >nul 2>&1
if %errorlevel%==0 (
    schtasks /run /tn OpenBrainHeartbeatAgent >nul 2>&1
    echo     Heartbeat agent: scheduled task triggered ^(pid-probe interval 60s^)
) else (
    start "" /B "%PYTHON%" "%OB_ROOT%\scripts\heartbeat_agent.py" >"%OB_ROOT%\logs\heartbeat-agent.log" 2>&1
    echo     Heartbeat agent: inline ^(install via scripts\windows\install-heartbeat-agent.ps1 for persistence^)
)

echo [6/6] Starting Ollama model monitor (v0.24.2+)...
schtasks /query /tn OpenBrainOllamaMonitor >nul 2>&1
if %errorlevel%==0 (
    schtasks /run /tn OpenBrainOllamaMonitor >nul 2>&1
    echo     Model monitor: scheduled task triggered ^(poll interval 5s^)
) else (
    start "" /B "%PYTHON%" "%OB_ROOT%\scripts\ollama_model_monitor.py" >"%OB_ROOT%\logs\ollama-model-events.jsonl" 2>"%OB_ROOT%\logs\model-monitor.log"
    echo     Model monitor: inline ^(install via scripts\windows\install-model-monitor.ps1 for persistence^)
)

echo.
echo Open Brain v0.24.2 is ON. HTTP transport -- reconnect without session restart.
echo   - Session registry with signoff + external heartbeat agent ^(no TTL^)
echo   - Cross-host session reaper ^(sweep_host admin tool, v0.24.0^)
echo   - Action-item compliance gate with kind field ^(task/rule, v0.24.0^)
echo   - Ollama model LOAD/UNLOAD/THRASH monitor ^(v0.24.2^)
echo   - Hybrid search ^(vector + full-text^), uptime-based decay, time-scoped search
echo   - Skills layer ^(conditional-load guardrails^), belief revision ^(supersede^)
