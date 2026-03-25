$desktop = [Environment]::GetFolderPath("Desktop")
$WshShell = New-Object -ComObject WScript.Shell

$s = $WshShell.CreateShortcut("$desktop\Open Brain ON.lnk")
$s.TargetPath = "F:\open-brain\scripts\windows\open-brain-on.cmd"
$s.WorkingDirectory = "F:\open-brain"
$s.Description = "Start Open Brain MCP server"
$s.IconLocation = "C:\Windows\System32\shell32.dll,21"
$s.Save()

$t = $WshShell.CreateShortcut("$desktop\Open Brain OFF.lnk")
$t.TargetPath = "F:\open-brain\scripts\windows\open-brain-off.cmd"
$t.WorkingDirectory = "F:\open-brain"
$t.Description = "Stop Open Brain MCP server"
$t.IconLocation = "C:\Windows\System32\shell32.dll,27"
$t.Save()

$p = $WshShell.CreateShortcut("$desktop\Open Brain SSE Proxy.lnk")
$p.TargetPath = "F:\open-brain\scripts\windows\open-brain-sse-proxy.cmd"
$p.WorkingDirectory = "F:\open-brain"
$p.Description = "Start Open Brain SSE Proxy"
$p.IconLocation = "C:\Windows\System32\shell32.dll,14"
$p.Save()

$d = $WshShell.CreateShortcut("$desktop\Open Brain Dashboard.lnk")
$d.TargetPath = "F:\open-brain\.venv\Scripts\pythonw.exe"
$d.Arguments = "F:\open-brain\dashboard.py"
$d.WorkingDirectory = "F:\open-brain"
$d.Description = "Open Brain monitoring dashboard"
$d.IconLocation = "F:\open-brain\assets\brain.ico"
$d.WindowStyle = 7
$d.Save()

Write-Host "Desktop shortcuts created."
