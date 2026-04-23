#Requires -Version 5.1
<#
.SYNOPSIS
    Remove the Open Brain Ollama model monitor scheduled task.
.DESCRIPTION
    Stops and unregisters the OpenBrainOllamaMonitor scheduled task.
    Safe to run even if the task doesn't exist — exits cleanly.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$TaskName = 'OpenBrainOllamaMonitor'

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "$TaskName not registered — nothing to do."
    exit 0
}

Write-Host "Stopping $TaskName..."
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Write-Host "Unregistering..."
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed $TaskName."
