@echo off
title Open Brain v2 — Container Startup
REM Starts the open-brain-v2-db Postgres container on port 5433.
REM Does NOT start the MCP server (that's spawned per-client via mcp_config).
REM Does NOT touch v1's open-brain-db container on port 5432.

set SCRIPT_DIR=%~dp0
set OB_ROOT=%SCRIPT_DIR%..\..
for %%I in ("%OB_ROOT%") do set OB_ROOT=%%~fI

echo Starting Open Brain v2 database container...
docker compose -f "%OB_ROOT%\docker-compose.v2.yml" up -d
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Failed to start v2 container. Is Docker running?
    timeout /t 5 >nul
    exit /b 1
)

echo.
echo Waiting for health check...
timeout /t 5 >nul

docker exec open-brain-v2-db pg_isready -U postgres -d open_brain_v2 >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo Open Brain v2 database is HEALTHY.
    echo   Container: open-brain-v2-db
    echo   Port: 5433
    echo   Database: open_brain_v2
) else (
    echo WARNING: Container started but health check failed. Give it a few more seconds.
)

echo.
echo v2 MCP server will start automatically when an agent connects via mcp_config.
timeout /t 3 >nul
