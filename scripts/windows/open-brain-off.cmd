@echo off
REM Shim — delegates to open-brain-off.ps1 (PowerShell 7).
REM Keep this .cmd so Explorer double-click and legacy callers still work.
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0open-brain-off.ps1"
