# Creates Desktop shortcuts for Open Brain. Resolves the repo root
# relative to this script's location so the shortcuts work regardless
# of where the repo is installed.

$OB = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$desktop = [Environment]::GetFolderPath("Desktop")
$WshShell = New-Object -ComObject WScript.Shell

$s = $WshShell.CreateShortcut("$desktop\Open Brain ON.lnk")
$s.TargetPath = "$OB\scripts\windows\open-brain-on.cmd"
$s.WorkingDirectory = $OB
$s.Description = "Start Open Brain MCP server"
$s.IconLocation = "C:\Windows\System32\shell32.dll,21"
$s.Save()

$t = $WshShell.CreateShortcut("$desktop\Open Brain OFF.lnk")
$t.TargetPath = "$OB\scripts\windows\open-brain-off.cmd"
$t.WorkingDirectory = $OB
$t.Description = "Stop Open Brain MCP server"
$t.IconLocation = "C:\Windows\System32\shell32.dll,27"
$t.Save()

$p = $WshShell.CreateShortcut("$desktop\Open Brain SSE Proxy.lnk")
$p.TargetPath = "$OB\scripts\windows\open-brain-sse-proxy.cmd"
$p.WorkingDirectory = $OB
$p.Description = "Start Open Brain SSE Proxy"
$p.IconLocation = "C:\Windows\System32\shell32.dll,14"
$p.Save()

$d = $WshShell.CreateShortcut("$desktop\Open Brain Dashboard.lnk")
$d.TargetPath = "$OB\.venv\Scripts\pythonw.exe"
$d.Arguments = "$OB\dashboard.py"
$d.WorkingDirectory = $OB
$d.Description = "Open Brain monitoring dashboard"
$d.IconLocation = "$OB\assets\brain.ico"
$d.WindowStyle = 7
$d.Save()

Write-Host "Desktop shortcuts created."
