@echo off
REM Resolve the repo root relative to this script's location.
set SCRIPT_DIR=%~dp0
set OB_ROOT=%SCRIPT_DIR%..\..
for %%I in ("%OB_ROOT%") do set OB_ROOT=%%~fI

REM If the dashboard is already running, focus its window and exit.
REM (Prevents a second instance from hanging on DB/file-handle conflicts.)
wmic process where "commandline like '%%dashboard.py%%' and not commandline like '%%wmic%%'" get processid /format:list 2>nul | findstr /I "ProcessId" >nul 2>&1
if %errorlevel%==0 (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0focus_dashboard.ps1" >nul 2>&1
    exit /b 0
)
start "" "%OB_ROOT%\.venv\Scripts\pythonw.exe" "%OB_ROOT%\dashboard.py"
