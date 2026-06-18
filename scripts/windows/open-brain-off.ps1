#Requires -Version 7.0
<#
.SYNOPSIS
    Stop Open Brain gracefully: unload models, stop MCP servers, stop DBs.
    Delegates to infrastructure.py for clean Python-side shutdown.
#>

$host.UI.RawUI.WindowTitle = 'Open Brain OFF'

$OB_ROOT = (Resolve-Path "$PSScriptRoot\..\..").Path
$PYTHON  = "$OB_ROOT\.venv\Scripts\python.exe"

Write-Host 'Stopping Open Brain gracefully...'
& $PYTHON -c "import sys; sys.path.insert(0, r'$OB_ROOT\scripts'); from infrastructure import bring_down; bring_down()"

Write-Host ''
Write-Host 'Open Brain is OFF. Docker Desktop left running (respects other containers).'
Start-Sleep 3
