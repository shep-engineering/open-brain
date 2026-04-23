@echo off
REM Wrapper for the Open Brain Ollama model monitor — invoked by the
REM scheduled task OpenBrainOllamaMonitor (see install-model-monitor.ps1).
REM
REM Polls ollama /api/ps every OLLAMA_POLL_SECONDS (default 5) and
REM emits LOAD / UNLOAD / THRASH events to logs/ollama-model-events.jsonl.
REM Stderr goes to %LOCALAPPDATA%\open-brain\model-monitor.log for
REM operational visibility. Process stays alive as long as the logon
REM session does; the task restarts it within 60s on exit.
setlocal EnableDelayedExpansion

set SCRIPT_DIR=%~dp0
set OB_ROOT=%SCRIPT_DIR%..\..
for %%I in ("%OB_ROOT%") do set OB_ROOT=%%~fI

set LOG_DIR=%LOCALAPPDATA%\open-brain
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" 2>nul
set LOG=%LOG_DIR%\model-monitor.log

set PYTHON=%OB_ROOT%\.venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python

"%PYTHON%" "%OB_ROOT%\scripts\ollama_model_monitor.py" >>"%LOG%" 2>&1
