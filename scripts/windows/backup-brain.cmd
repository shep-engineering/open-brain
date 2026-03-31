@echo off
REM Daily backup of Open Brain PostgreSQL database
REM Add to Windows Task Scheduler to run daily

set BACKUP_DIR=F:\open-brain\backups
set CONTAINER=open-brain-db-1

for /f "tokens=1-3 delims=/ " %%a in ('date /t') do set DATESTAMP=%%c%%a%%b
for /f "tokens=1-2 delims=: " %%a in ('time /t') do set TIMESTAMP=%%a%%b

if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

docker exec %CONTAINER% pg_dump -U postgres openbrain > "%BACKUP_DIR%\brain-%DATESTAMP%-%TIMESTAMP%.sql" 2>nul

if %errorlevel% equ 0 (
    echo [backup] OK: %BACKUP_DIR%\brain-%DATESTAMP%-%TIMESTAMP%.sql
) else (
    echo [backup] FAILED
    del "%BACKUP_DIR%\brain-%DATESTAMP%-%TIMESTAMP%.sql" 2>nul
    exit /b 1
)
