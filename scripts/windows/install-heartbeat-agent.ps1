#Requires -Version 5.1
<#
.SYNOPSIS
    Register the Open Brain heartbeat agent as a Windows scheduled task.

.DESCRIPTION
    The heartbeat agent pid-probes active_sessions rows every 60s and
    marks dead processes 'ended'. Without this task registered, the
    agent only runs while a foreground shell (open-brain-on.cmd) keeps
    it alive — so after a reboot or crash the registry silently goes
    stale and boot_session surfaces dead sessions as live siblings.

    Runs in the current user's interactive logon context — does NOT
    require admin elevation unless the repo lives in a protected path.
    Task starts immediately on install and again at every logon;
    restarts within 60s if the agent exits.

.NOTES
    Idempotent: re-running replaces any existing task of the same name.
    Logs:     %LOCALAPPDATA%\open-brain\heartbeat-agent.log
    Query:    schtasks /query /tn OpenBrainHeartbeatAgent /v /fo list
    Uninstall: .\uninstall-heartbeat-agent.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$TaskName = 'OpenBrainHeartbeatAgent'
$ObRoot   = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Wrapper  = Join-Path $ObRoot 'scripts\windows\run-heartbeat-agent.cmd'

if (-not (Test-Path $Wrapper)) {
    throw "Heartbeat wrapper not found at $Wrapper"
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
    -Description 'Open Brain session-registry heartbeat agent. Probes active_sessions.pid rows every 60s and marks dead processes ended.' `
    | Out-Null

Write-Host "Registered $TaskName."
Start-ScheduledTask -TaskName $TaskName
Write-Host "Started. Check logs at $env:LOCALAPPDATA\open-brain\heartbeat-agent.log"
