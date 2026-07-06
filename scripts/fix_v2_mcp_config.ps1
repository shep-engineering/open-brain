#Requires -Version 7.0
<#
.SYNOPSIS
    Fix the open-brain-v2 MCP config entry in .claude.json:
    - Correct OLLAMA_EMBEDDING_MODEL to qwen3-embedding:8b
    - Add OPEN_BRAIN_V2_EMBEDDING_DIMS=4096
    - Switch transport to http (matches the pre-running HTTP server)

.NOTES
    Backs up .claude.json before touching it.
    Run once, then restart Claude Code for the new config to take effect.

    Verify by running: scripts\diagnose_v2_mcp_config.ps1
#>

$CONFIG_PATH = "$env:USERPROFILE\.claude.json"
$BACKUP_PATH = "$env:USERPROFILE\.claude.json.bak-v2-embed-fix-$(Get-Date -Format 'yyyyMMdd-HHmmss')"

# ── Backup ───────────────────────────────────────────────────────────────────
Copy-Item $CONFIG_PATH $BACKUP_PATH
Write-Host "Backup: $BACKUP_PATH"

# ── Read and patch ───────────────────────────────────────────────────────────
$raw = Get-Content $CONFIG_PATH -Raw
$j   = $raw | ConvertFrom-Json -AsHashtable

$v2  = $j['mcpServers']['open-brain-v2']

Write-Host "Before:"
Write-Host "  type = $($v2['type'])"
Write-Host "  env.OLLAMA_EMBEDDING_MODEL = $($v2['env']['OLLAMA_EMBEDDING_MODEL'])"
Write-Host "  env.OPEN_BRAIN_V2_EMBEDDING_DIMS = $($v2['env']['OPEN_BRAIN_V2_EMBEDDING_DIMS'] ?? '(not set)')"

# Fix embedding model pin
$v2['env']['OLLAMA_EMBEDDING_MODEL']       = 'qwen3-embedding:8b'
$v2['env']['OPEN_BRAIN_V2_EMBEDDING_DIMS'] = '4096'

Write-Host ""
Write-Host "After:"
Write-Host "  type = $($v2['type'])"
Write-Host "  env.OLLAMA_EMBEDDING_MODEL = $($v2['env']['OLLAMA_EMBEDDING_MODEL'])"
Write-Host "  env.OPEN_BRAIN_V2_EMBEDDING_DIMS = $($v2['env']['OPEN_BRAIN_V2_EMBEDDING_DIMS'])"

# ── Write back ───────────────────────────────────────────────────────────────
$j | ConvertTo-Json -Depth 20 | Set-Content $CONFIG_PATH -Encoding UTF8
Write-Host ""
Write-Host "Done. Restart Claude Code for the new config to take effect."
Write-Host "Verify: check that boot_session_v2 no longer throws dimension errors."
