#Requires -Version 5.1
<#
.SYNOPSIS
    Register the Open Brain Ollama model monitor as a Windows scheduled task.

.DESCRIPTION
    The monitor polls Ollama's /api/ps every 5s and emits one JSON line
    per state transition (LOAD / UNLOAD / THRASH) to
    logs/ollama-model-events.jsonl. Purpose: detect model-reload
    thrashing early — tight LOAD-after-UNLOAD cycles waste GPU time and
    burn wall-clock on every affected tool call.

    Runs in the current user's interactive logon context — does NOT
    require admin elevation unless the repo lives in a protected path.
    Task starts immediately on install and again at every logon;
    restarts within 60s if the monitor exits.

.NOTES
    Idempotent: re-running replaces any existing task of the same name.
    Logs (stderr): %LOCALAPPDATA%\open-brain\model-monitor.log
    Events (JSONL): <repo>\logs\ollama-model-events.jsonl
    Query:      schtasks /query /tn OpenBrainOllamaMonitor /v /fo list
    Uninstall:  .\uninstall-model-monitor.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$TaskName = 'OpenBrainOllamaMonitor'
$ObRoot   = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Wrapper  = Join-Path $ObRoot 'scripts\windows\run-model-monitor.cmd'

if (-not (Test-Path $Wrapper)) {
    throw "Model-monitor wrapper not found at $Wrapper"
}

Write-Host "Repo root: $ObRoot"
Write-Host "Wrapper:   $Wrapper"

$Action = New-ScheduledTaskAction -Execute $Wrapper -WorkingDirectory $ObRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)
$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing task $TaskName..."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description 'Open Brain Ollama model monitor. Polls /api/ps every 5s and emits LOAD/UNLOAD/THRASH events to logs/ollama-model-events.jsonl.' `
    | Out-Null

Write-Host "Registered $TaskName."
Start-ScheduledTask -TaskName $TaskName
Write-Host "Started. Log: $env:LOCALAPPDATA\open-brain\model-monitor.log"
Write-Host "Events:      $ObRoot\logs\ollama-model-events.jsonl"
