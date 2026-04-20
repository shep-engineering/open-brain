@echo off
REM Wrapper for the Open Brain heartbeat agent — invoked by the scheduled
REM task OpenBrainHeartbeatAgent (see install-heartbeat-agent.ps1).
REM
REM Resolves the repo root relative to this file, runs the venv python
REM (or system python as fallback), and pipes output to a per-user log
REM file under %LOCALAPPDATA%. The process stays alive as long as the
REM user's logon session does; the scheduled task restarts it within
REM 60s if it ever exits.
setlocal EnableDelayedExpansion

set SCRIPT_DIR=%~dp0
set OB_ROOT=%SCRIPT_DIR%..\..
for %%I in ("%OB_ROOT%") do set OB_ROOT=%%~fI

set LOG_DIR=%LOCALAPPDATA%\open-brain
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" 2>nul
set LOG=%LOG_DIR%\heartbeat-agent.log

set PYTHON=%OB_ROOT%\.venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python

"%PYTHON%" "%OB_ROOT%\scripts\heartbeat_agent.py" >>"%LOG%" 2>&1
