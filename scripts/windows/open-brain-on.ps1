#Requires -Version 7.0
<#
.SYNOPSIS
    Start all Open Brain services: Docker DBs, Ollama, V1+V2 MCP servers,
    heartbeat agent, and model monitor.
.NOTES
    Requires PowerShell 7.6+ (pwsh). Run via open-brain-on.cmd shim or directly.
#>

$host.UI.RawUI.WindowTitle = 'Open Brain ON'

# ── Resolve paths ───────────────────────────────────────────────────────────
$OB_ROOT = (Resolve-Path "$PSScriptRoot\..\..").Path
$PYTHON  = "$OB_ROOT\.venv\Scripts\python.exe"
$V1_DSN  = 'postgresql://postgres:password@127.0.0.1:5432/openbrain'
$V2_DSN  = 'postgresql://postgres:password@127.0.0.1:5433/open_brain_v2'

New-Item -ItemType Directory -Force "$OB_ROOT\logs" | Out-Null
Write-Host 'Starting Open Brain MCP server...'

# ── Helpers ─────────────────────────────────────────────────────────────────
function Wait-Condition {
    param([scriptblock]$Test, [int]$MaxTries = 15, [int]$DelaySec = 2)
    for ($i = 0; $i -lt $MaxTries; $i++) {
        if (& $Test) { return $true }
        Start-Sleep $DelaySec
    }
    return $false
}

function Test-TcpPort {
    param([int]$Port)
    try { $c = [Net.Sockets.TcpClient]::new('127.0.0.1', $Port); $c.Close(); return $true }
    catch { return $false }
}

# ── [1/6] Docker Desktop ────────────────────────────────────────────────────
Write-Host '[1/6] Starting Docker Desktop (if needed)...'
docker info 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Start-Process 'C:\Program Files\Docker\Docker\Docker Desktop.exe' -ErrorAction SilentlyContinue
    Write-Host '    Waiting for Docker to start...'
    $ok = Wait-Condition { docker info 2>$null | Out-Null; $LASTEXITCODE -eq 0 }
    if ($ok) { Write-Host '    Docker ready' }
    else     { Write-Host '    Docker not responding after 30s — open Docker Desktop manually' }
} else {
    Write-Host '    Docker already running'
}

# ── [2/6] PostgreSQL containers ─────────────────────────────────────────────
Write-Host '[2/6] Checking open-brain-db and open-brain-v2-db (Docker)...'
docker start open-brain-db 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { Write-Host '    open-brain-db container OK' }
else                      { Write-Host '    open-brain-db FAILED — is Docker running?' }

docker start open-brain-v2-db 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { Write-Host '    open-brain-v2-db container OK' }
else                      { Write-Host '    open-brain-v2-db FAILED — is Docker running?' }

Write-Host '    Waiting for PostgreSQL to accept connections...'

$ok1 = Wait-Condition { & $PYTHON -c "import psycopg2; psycopg2.connect('$V1_DSN', connect_timeout=2).close()" 2>$null; $LASTEXITCODE -eq 0 }
if ($ok1) { Write-Host '    open-brain-db PostgreSQL ready' }
else       { Write-Host '    WARNING: open-brain-db PostgreSQL not responding after 30s — server may fail' }

$ok2 = Wait-Condition { & $PYTHON -c "import psycopg2; psycopg2.connect('$V2_DSN', connect_timeout=2).close()" 2>$null; $LASTEXITCODE -eq 0 }
if ($ok2) { Write-Host '    open-brain-v2-db PostgreSQL ready' }
else       { Write-Host '    WARNING: open-brain-v2-db PostgreSQL not responding after 30s — server may fail' }

# ── [3/6] Ollama ─────────────────────────────────────────────────────────────
Write-Host '[3/6] Checking Ollama...'
try {
    Invoke-WebRequest 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 -ErrorAction Stop | Out-Null
    Write-Host '    Ollama already running'
} catch {
    Write-Host '    Starting Ollama (dual GPU, LAN-exposed on 0.0.0.0:11434)...'
    $env:OLLAMA_HOST             = '0.0.0.0:11434'
    $env:OLLAMA_NUM_GPU          = '2'
    $env:CUDA_VISIBLE_DEVICES    = '0,1'
    $env:OLLAMA_KEEP_ALIVE       = '30m'
    $env:OLLAMA_MAX_LOADED_MODELS = '2'
    Start-Process 'ollama' -ArgumentList 'serve' -WindowStyle Hidden `
        -RedirectStandardOutput "$OB_ROOT\logs\ollama.log" `
        -RedirectStandardError  "$OB_ROOT\logs\ollama-err.log"
    Write-Host '    Ollama started (dual GPU, max 2 models loaded, bound 0.0.0.0:11434)'
}

# ── [4/6] MCP servers ────────────────────────────────────────────────────────
Write-Host '[4/6] Starting Open Brain MCP servers (HTTP transport)...'
foreach ($port in 8080, 8081) {
    Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}

# Use ProcessStartInfo directly: UseShellExecute=false + CreateNoWindow=true
# gives a fully detached process that survives the parent pwsh exit.
# Do NOT use Start-Process -RedirectStandardError — that creates a PS-managed
# pipe whose read end closes when pwsh exits, breaking uvicorn's stderr writes
# and causing the server to crash on the first log line after parent exit.
# Servers log internally to logs/server.log and logs/brain_v2.log.
foreach ($pair in @(
    @{ Script = "$OB_ROOT\server.py";          Port = 8080 },
    @{ Script = "$OB_ROOT\brain_v2\server.py"; Port = 8081 }
)) {
    $psi = [System.Diagnostics.ProcessStartInfo]::new($PYTHON)
    $psi.Arguments   = "`"$($pair.Script)`" --transport http --port $($pair.Port)"
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow  = $true
    [System.Diagnostics.Process]::Start($psi) | Out-Null
}

Write-Host '    Waiting for HTTP servers to accept connections...'
$v1ok = Wait-Condition { Test-TcpPort 8080 }
$v2ok = Wait-Condition { Test-TcpPort 8081 }
if ($v1ok) { Write-Host '    open-brain v1 HTTP ready (port 8080)' }
else        { Write-Host '    WARNING: open-brain v1 not responding — check logs\server-v1-crash.log' }
if ($v2ok) { Write-Host '    open-brain v2 HTTP ready (port 8081)' }
else        { Write-Host '    WARNING: open-brain v2 not responding — check logs\server-v2-crash.log' }

# ── [5/6] Heartbeat agent ────────────────────────────────────────────────────
Write-Host '[5/6] Starting session-registry heartbeat agent (v0.14.0+)...'
schtasks /query /tn OpenBrainHeartbeatAgent 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    schtasks /run /tn OpenBrainHeartbeatAgent 2>$null | Out-Null
    Write-Host '    Heartbeat agent: scheduled task triggered (pid-probe interval 60s)'
} else {
    Start-Process $PYTHON -ArgumentList "$OB_ROOT\scripts\heartbeat_agent.py" `
        -WindowStyle Hidden -RedirectStandardOutput "$OB_ROOT\logs\heartbeat-agent.log" `
        -RedirectStandardError "$OB_ROOT\logs\heartbeat-agent-err.log"
    Write-Host '    Heartbeat agent: inline (install via scripts\windows\install-heartbeat-agent.ps1 for persistence)'
}

# ── [6/6] Ollama model monitor ───────────────────────────────────────────────
Write-Host '[6/6] Starting Ollama model monitor (v0.24.2+)...'
schtasks /query /tn OpenBrainOllamaMonitor 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    schtasks /run /tn OpenBrainOllamaMonitor 2>$null | Out-Null
    Write-Host '    Model monitor: scheduled task triggered (poll interval 5s)'
} else {
    Start-Process $PYTHON -ArgumentList "$OB_ROOT\scripts\ollama_model_monitor.py" `
        -WindowStyle Hidden `
        -RedirectStandardOutput "$OB_ROOT\logs\ollama-model-events.jsonl" `
        -RedirectStandardError  "$OB_ROOT\logs\model-monitor.log"
    Write-Host '    Model monitor: inline (install via scripts\windows\install-model-monitor.ps1 for persistence)'
}

Write-Host ''
Write-Host 'Open Brain v0.27.0 is ON. HTTP transport — reconnect without session restart.'
Write-Host '  - V1 (port 8080) + V2 (port 8081) MCP servers'
Write-Host '  - Embeddings: qwen3-embedding:8b (4096d, MTEB 70.58)'
Write-Host '  - Session registry + external heartbeat agent (no TTL)'
Write-Host '  - Skills layer, belief revision (supersede), action-item compliance gate'
Write-Host '  - Hybrid search (vector + full-text), uptime-based decay, time-scoped search'
Start-Sleep 3
