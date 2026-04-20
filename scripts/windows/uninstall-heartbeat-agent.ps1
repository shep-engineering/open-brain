#Requires -Version 5.1
<#
.SYNOPSIS
    Remove the Open Brain heartbeat agent scheduled task.

.DESCRIPTION
    Stops and unregisters the `OpenBrainHeartbeatAgent` scheduled task
    created by install-heartbeat-agent.ps1. Idempotent — a no-op if the
    task is not present.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$TaskName = 'OpenBrainHeartbeatAgent'

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "$TaskName not registered. Nothing to do."
    return
}

Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed $TaskName."
