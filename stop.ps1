# Open Brain — Stop all services

$OB = Split-Path -Parent $MyInvocation.MyCommand.Path
$PID_FILE = "$OB\.open-brain.pid"

Write-Host ""

if (Test-Path $PID_FILE) {
    $stopped = 0
    Get-Content $PID_FILE | ForEach-Object {
        $p = $_.Trim()
        if ($p -and (Get-Process -Id $p -ErrorAction SilentlyContinue)) {
            # Also kill child processes (ngrok spawned by rest_api.py)
            Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq [int]$p } | ForEach-Object {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            }
            Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
            $stopped++
        }
    }
    Remove-Item $PID_FILE -Force
    Write-Host "  Open Brain stopped ($stopped processes)." -ForegroundColor Cyan
} else {
    Write-Host "  No running Open Brain found." -ForegroundColor Gray
}

# Also kill any stray ngrok
Get-Process -Name "ngrok" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

Write-Host ""
