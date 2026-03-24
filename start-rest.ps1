# Open Brain — Start REST API only (with tunnel)
# For when you just need remote access without the MCP stdio server.

param([switch]$NoTunnel)

& "$PSScriptRoot\start.ps1" -RestOnly $(if ($NoTunnel) { "-NoTunnel" })
