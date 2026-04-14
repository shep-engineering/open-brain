# Open Brain — Start all services
# Starts: MCP stdio server + REST API + ngrok tunnel
#
# Usage:
#   .\start.ps1              # start everything
#   .\start.ps1 -NoTunnel    # skip ngrok tunnel
#   .\start.ps1 -RestOnly    # REST API only (no MCP stdio)

param(
    [switch]$NoTunnel,
    [switch]$RestOnly
)

$ErrorActionPreference = "Continue"
$OB = Split-Path -Parent $MyInvocation.MyCommand.Path
$PYTHON = "$OB\.venv\Scripts\python.exe"
$PID_FILE = "$OB\.open-brain.pid"

Write-Host ""
Write-Host "  Open Brain - Starting services..." -ForegroundColor Cyan
Write-Host ""

# Check prerequisites
if (-not (Test-Path $PYTHON)) {
    Write-Host "  ERROR: Python venv not found at $PYTHON" -ForegroundColor Red
    Write-Host "  Run: python -m venv $OB\.venv && $OB\.venv\Scripts\pip install -r $OB\requirements.txt" -ForegroundColor Yellow
    exit 1
}

# Kill any existing Open Brain processes
if (Test-Path $PID_FILE) {
    Get-Content $PID_FILE | ForEach-Object {
        $p = $_.Trim()
        if ($p -and (Get-Process -Id $p -ErrorAction SilentlyContinue)) {
            Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
            Write-Host "  Stopped old process: $p" -ForegroundColor Gray
        }
    }
    Remove-Item $PID_FILE -Force
}

$pids = @()

if ($RestOnly) {
    # REST API only
    $tunnelFlag = if ($NoTunnel) { "--no-tunnel" } else { "" }
    $rest = Start-Process -FilePath $PYTHON -ArgumentList "$OB\rest_api.py $tunnelFlag" `
        -WorkingDirectory $OB -PassThru -WindowStyle Normal
    $pids += $rest.Id
    Write-Host "  REST API started (PID: $($rest.Id))" -ForegroundColor Green
} else {
    # Full stack: server.py --transport all
    $transport = if ($NoTunnel) { "both" } else { "all" }
    $server = Start-Process -FilePath $PYTHON -ArgumentList "$OB\server.py --transport $transport" `
        -WorkingDirectory $OB -PassThru -WindowStyle Normal
    $pids += $server.Id
    Write-Host "  Server started with --transport $transport (PID: $($server.Id))" -ForegroundColor Green
}

# Save PIDs for stop script
$pids | Out-File -FilePath $PID_FILE -Encoding ascii
Write-Host ""

# Wait for REST API to be ready
$ready = $false
for ($i = 0; $i -lt 10; $i++) {
    Start-Sleep -Seconds 2
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:8765/health" -TimeoutSec 3
        $ready = $true
        break
    } catch {}
}

if ($ready) {
    Write-Host "  ============================================" -ForegroundColor Yellow
    Write-Host "  Open Brain is running!" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Local:  http://localhost:8765" -ForegroundColor White
    if ($health.public_url) {
        Write-Host "  Public: $($health.public_url)" -ForegroundColor White
        Write-Host "  Docs:   $($health.public_url)/docs" -ForegroundColor Gray
        Write-Host ""
        Write-Host "  Tell remote clients:" -ForegroundColor Cyan
        Write-Host "  POST $($health.public_url)/pair" -ForegroundColor White
        Write-Host '  {"client_name": "your-tool-name"}' -ForegroundColor White
        $health.public_url | Set-Clipboard
        Write-Host ""
        Write-Host "  (Public URL copied to clipboard)" -ForegroundColor Green
    } else {
        Write-Host "  No tunnel (use -NoTunnel flag or configure ngrok)" -ForegroundColor Gray
    }
    Write-Host "  ============================================" -ForegroundColor Yellow
} else {
    Write-Host "  WARNING: REST API did not respond in time." -ForegroundColor Yellow
    Write-Host "  Check the server window for errors." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  Stop with: .\stop.ps1" -ForegroundColor Gray
Write-Host ""
