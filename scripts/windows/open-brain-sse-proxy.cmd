@echo off
REM Resolve the repo root relative to this script's location.
set SCRIPT_DIR=%~dp0
set OB_ROOT=%SCRIPT_DIR%..\..
for %%I in ("%OB_ROOT%") do set OB_ROOT=%%~fI

pushd "%OB_ROOT%"
start "" /min "%OB_ROOT%\.venv\Scripts\python.exe" -m mcp.server.sse --port 8765 -- python server.py
popd
