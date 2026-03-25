@echo off
title Open Brain OFF
echo Stopping Open Brain and freeing resources...

echo [1/4] Stopping Open Brain MCP server (WSL tmux)...
wsl -e bash -lc "tmux kill-session -t openbrain 2>/dev/null" >nul 2>&1
if %errorlevel%==0 (echo     Open Brain server stopped) else (echo     Server was not running)

echo [2/4] Stopping open-brain-db (Docker)...
docker stop open-brain-db >nul 2>&1
if %errorlevel%==0 (echo     open-brain-db stopped) else (echo     open-brain-db was not running)

echo [3/4] Unloading Ollama models and stopping Ollama...
ollama stop >nul 2>&1
timeout /t 2 >nul
taskkill /IM ollama.exe /F >nul 2>&1
echo     Ollama stopped

echo [4/4] Stopping Docker Desktop and WSL...
taskkill /IM "Docker Desktop.exe" /F >nul 2>&1
wsl --shutdown >nul 2>&1
echo     Docker/WSL stopped

echo.
echo Open Brain is OFF. VRAM and resources freed.
timeout /t 3 >nul
