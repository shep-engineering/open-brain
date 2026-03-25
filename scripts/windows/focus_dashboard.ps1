$proc = Get-Process pythonw -ErrorAction SilentlyContinue | Sort-Object WS -Descending | Select-Object -First 1
if ($proc -and $proc.MainWindowHandle -ne 0) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class Win32 {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
}
"@
    [Win32]::ShowWindow($proc.MainWindowHandle, 9)
    [Win32]::SetForegroundWindow($proc.MainWindowHandle)
    Write-Host "Focused PID $($proc.Id), HWND $($proc.MainWindowHandle)"
} else {
    Write-Host "No pythonw window found (MainWindowHandle=0). Dashboard may not have a visible window."
    Get-Process pythonw -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "PID $($_.Id) WS=$($_.WS) HWND=$($_.MainWindowHandle)" }
}
