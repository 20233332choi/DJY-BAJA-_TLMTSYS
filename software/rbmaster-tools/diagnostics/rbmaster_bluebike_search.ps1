$ErrorActionPreference = 'Stop'

Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;

public static class NativeBlueBikeSearch {
    public delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hwnd);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int maxCount);

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hwnd, int command);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hwnd);

    [DllImport("user32.dll")]
    public static extern void keybd_event(byte virtualKey, byte scanCode, uint flags, UIntPtr extraInfo);
}
'@

function Get-ProcessWindows([int]$ProcessId) {
    $items = [System.Collections.Generic.List[object]]::new()
    $callback = [NativeBlueBikeSearch+EnumWindowsProc]{
        param([IntPtr]$hwnd, [IntPtr]$lParam)
        [uint32]$owner = 0
        [void][NativeBlueBikeSearch]::GetWindowThreadProcessId($hwnd, [ref]$owner)
        if ($owner -eq $ProcessId -and [NativeBlueBikeSearch]::IsWindowVisible($hwnd)) {
            $text = [Text.StringBuilder]::new(512)
            [void][NativeBlueBikeSearch]::GetWindowText($hwnd, $text, $text.Capacity)
            $items.Add([pscustomobject]@{ Handle = $hwnd.ToInt64(); Title = $text.ToString() })
        }
        return $true
    }
    [void][NativeBlueBikeSearch]::EnumWindows($callback, [IntPtr]::Zero)
    return $items
}

$process = Get-Process -Name RBMASTERPRO -ErrorAction Stop | Select-Object -First 1
$windows = @(Get-ProcessWindows $process.Id)
$main = $windows | Where-Object Title -Like 'Rapid Bike Master*' | Select-Object -First 1
if (-not $main) { throw "RB Master main window not found" }

$handle = [IntPtr]::new([int64]$main.Handle)
[void][NativeBlueBikeSearch]::ShowWindow($handle, 9)
[void][NativeBlueBikeSearch]::SetForegroundWindow($handle)
Start-Sleep -Milliseconds 700

$KEYUP = 0x0002
[NativeBlueBikeSearch]::keybd_event(0x11, 0, 0, [UIntPtr]::Zero) # Ctrl
[NativeBlueBikeSearch]::keybd_event(0x10, 0, 0, [UIntPtr]::Zero) # Shift
[NativeBlueBikeSearch]::keybd_event(0x42, 0, 0, [UIntPtr]::Zero) # B
[NativeBlueBikeSearch]::keybd_event(0x42, 0, $KEYUP, [UIntPtr]::Zero)
[NativeBlueBikeSearch]::keybd_event(0x10, 0, $KEYUP, [UIntPtr]::Zero)
[NativeBlueBikeSearch]::keybd_event(0x11, 0, $KEYUP, [UIntPtr]::Zero)

Start-Sleep -Seconds 8
$after = @(Get-ProcessWindows $process.Id)
@(
    "Shortcut sent to RBMASTERPRO PID $($process.Id)"
    "Before:"
    ($windows | ForEach-Object { "  $($_.Handle) [$($_.Title)]" })
    "After:"
    ($after | ForEach-Object { "  $($_.Handle) [$($_.Title)]" })
) | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'bluebike_search_result.txt') -Encoding UTF8
