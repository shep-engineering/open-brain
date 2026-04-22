# plan-grep.ps1 — Search the sibling planning repo (Windows-native).
#
# Open-brain's planning docs live in degailen/open-brain-planning (private),
# cloned as a sibling directory at ..\open-brain-planning on disk. The public
# mirror of open-brain deliberately omits those docs, so we can't just grep
# .\docs\planning\ anymore.
#
# Usage:
#   powershell scripts\plan-grep.ps1 -Pattern "registry trust"
#   powershell scripts\plan-grep.ps1 -Pattern "contributor" -CaseSensitive
#
# Env override:
#   $env:PLAN_DIR = "D:\alt\open-brain-planning"; powershell scripts\plan-grep.ps1 ...
#
# Fails loudly with a clone hint if the sibling repo is not present.

[CmdletBinding()]
param(
    [Parameter(Mandatory, Position=0)]
    [string]$Pattern,

    [switch]$CaseSensitive
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$PlanDir  = if ($env:PLAN_DIR) { $env:PLAN_DIR } else { Join-Path (Split-Path -Parent $RepoRoot) 'open-brain-planning' }

if (-not (Test-Path $PlanDir -PathType Container)) {
    Write-Error @"
Planning repo not found at: $PlanDir

Clone it alongside open-brain on disk:
  git clone git@github-degailen:degailen/open-brain-planning.git `"$PlanDir`"

Or set `$env:PLAN_DIR to an alternate location.
"@
    exit 1
}

$selectParams = @{
    Pattern     = $Pattern
    AllMatches  = $true
}
if (-not $CaseSensitive) {
    $selectParams['SimpleMatch'] = $false
    # Select-String is case-insensitive by default; no flag needed unless -CaseSensitive specified.
}

Get-ChildItem $PlanDir -Recurse -File |
    Where-Object { $_.FullName -notmatch '\\\.git\\' } |
    Select-String @selectParams |
    ForEach-Object { "{0}:{1}: {2}" -f $_.Path, $_.LineNumber, $_.Line.Trim() }
