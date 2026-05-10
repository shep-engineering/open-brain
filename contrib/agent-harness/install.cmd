@echo off
:: install.cmd — Install the Open Brain agent harness hooks on Windows.
:: Run from the open-brain repo root: contrib\agent-harness\install.cmd
::
:: What it does:
::   1. Copies hook files to %USERPROFILE%\.claude\hooks\
::   2. Prints the settings.json snippet with correct paths substituted
::
:: After running, manually merge the printed snippet into
:: %USERPROFILE%\.claude\settings.json

setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "HOOKS_SRC=%SCRIPT_DIR%hooks"
set "HOOKS_DEST=%USERPROFILE%\.claude\hooks"

echo.
echo Open Brain Agent Harness -- installer (Windows)
echo Destination: %HOOKS_DEST%
echo.

if not exist "%HOOKS_DEST%" mkdir "%HOOKS_DEST%"

:: ── Copy hooks ────────────────────────────────────────────────────────────────
set HOOKS=branch-guard.sh no-force-push.sh no-rm-rf.sh require-brain-boot.sh require-prework.sh require-brain-save.sh require-brain-checkpoint.sh detect-correction.sh session-end-save.py

for %%H in (%HOOKS%) do (
    if exist "%HOOKS_SRC%\%%H" (
        copy /Y "%HOOKS_SRC%\%%H" "%HOOKS_DEST%\%%H" >nul
        echo   [DONE] %%H  --^>  %HOOKS_DEST%\%%H
    ) else (
        echo   [SKIP] %%H (not found in %HOOKS_SRC%)
    )
)

:: ── Print settings.json snippet with paths substituted ───────────────────────
echo.
echo ==========================================================
echo  Next step: merge this into %%USERPROFILE%%\.claude\settings.json
echo ==========================================================
echo.
echo The hooks path to use in settings.json:
echo   %HOOKS_DEST%
echo.
echo Example entry for require-brain-boot.sh:
echo   "command": "bash \"%HOOKS_DEST:\=\\%\\require-brain-boot.sh\""
echo.
echo See settings.snippet.json in this directory for the full structure.
echo Replace HOOKS_DIR with: %HOOKS_DEST:\=\\%
echo.
echo NOTE: require-prework.sh only enforces in repos that have a
echo .task-markers\ directory. To opt in a repo:
echo   mkdir path\to\your\repo\.task-markers
echo   echo .task-markers/ ^>^> path\to\your\repo\.gitignore
echo.
echo Then run scripts\pre-work-check.sh before starting work in that repo.
echo.

endlocal
