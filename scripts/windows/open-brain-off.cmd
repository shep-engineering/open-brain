@echo off
title Open Brain OFF

REM Resolve the repo root relative to this script's location, then
REM delegate to the pure-Python graceful shutdown (infrastructure.py).
REM That path unloads ollama models cleanly, Ctrl+Break's the server
REM (if we own it), stops the open-brain-db container, and leaves
REM Docker Desktop running so unrelated containers survive. See
REM scripts/infrastructure.py for the full behavior.
set SCRIPT_DIR=%~dp0
set OB_ROOT=%SCRIPT_DIR%..\..
for %%I in ("%OB_ROOT%") do set OB_ROOT=%%~fI

echo Stopping Open Brain gracefully...
"%OB_ROOT%\.venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, r'%OB_ROOT%\scripts'); from infrastructure import bring_down; bring_down()"

echo.
echo Open Brain is OFF. Docker Desktop left running (respects other containers).
timeout /t 3 >nul
