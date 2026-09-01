# POC: push text into a merge tool's OUTPUT pane from outside the process.
#
# WM_SETTEXT is already ruled out -- TTextEditor is custom-drawn and renders
# from its own buffer, so WM_SETTEXT only sets the window's text property
# (which is how a naive check reports a false success). The remaining route is
# the one a human uses: focus the pane, select all, paste.
#
# Focus is the hard part. SetFocus only works within the caller's input queue,
# so the thread must be attached to the target's first. That, and not the
# keystrokes, is what makes this approach fragile.
#
# Everything is scoped to the window handle passed in. The clipboard is saved
# and restored.

param(
  [Parameter(Mandatory=$true)][string]$Pane,     # the OUTPUT TTextEditor
  [Parameter(Mandatory=$true)][string]$Top,      # its top-level window
  [string]$OutFile = "",                         # verify by reading this after Ctrl+S
  [switch]$Save                                  # send Ctrl+S as well
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class Inj {
  [DllImport("user32.dll")] public static extern IntPtr SetFocus(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetFocus();
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr pid);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool attach);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassNameW(IntPtr h, StringBuilder s, int n);
  public static string Cls(IntPtr h){ var s=new StringBuilder(256); GetClassNameW(h,s,256); return s.ToString(); }
}
"@ -ErrorAction SilentlyContinue

$pane = [IntPtr][Convert]::ToInt64($Pane, 16)
$top  = [IntPtr][Convert]::ToInt64($Top, 16)
$MARK = "ZZZ-INJECTED-BY-CCS-POC-ZZZ"
$payload = "$MARK`r`nsecond line of the injected payload`r`n$MARK-END"

$saved = $null
try { $saved = Get-Clipboard -Raw -EA SilentlyContinue } catch {}
"  clipboard saved ($(if($saved){$saved.Length}else{0}) chars)"

try {
    [void][Inj]::SetForegroundWindow($top)
    Start-Sleep -Milliseconds 400

    # Attach our input queue to the target's so SetFocus can cross processes.
    $tid  = [Inj]::GetWindowThreadProcessId($top, [IntPtr]::Zero)
    $me   = [Inj]::GetCurrentThreadId()
    $ok   = [Inj]::AttachThreadInput($me, $tid, $true)
    "  AttachThreadInput -> $ok"
    [void][Inj]::SetFocus($pane)
    $focused = [IntPtr][Inj]::GetFocus()
    $fHex = "0x" + ([int64]$focused).ToString("X")
    $pHex = "0x" + ([int64]$pane).ToString("X")
    "  focus is now $fHex (" + [Inj]::Cls($focused) + ") -- wanted $pHex"
    $hit = ([int64]$focused -eq [int64]$pane)
    [void][Inj]::AttachThreadInput($me, $tid, $false)

    if (-not $hit) { "  FOCUS FAILED -- not sending keystrokes into the wrong control"; return }

    Set-Clipboard -Value $payload
    Start-Sleep -Milliseconds 150
    [System.Windows.Forms.SendKeys]::SendWait("^a")
    Start-Sleep -Milliseconds 150
    [System.Windows.Forms.SendKeys]::SendWait("^v")
    Start-Sleep -Milliseconds 500
    "  sent Ctrl+A, Ctrl+V"

    if ($Save) {
        [System.Windows.Forms.SendKeys]::SendWait("^s")
        Start-Sleep -Milliseconds 1000
        "  sent Ctrl+S"
    }
}
finally {
    # Restore what was there. An EMPTY clipboard is restored by clearing it --
    # writing " " (the first version) was a destructive "restore".
    if ($null -ne $saved) { Set-Clipboard -Value $saved } else { [System.Windows.Forms.Clipboard]::Clear() }
    "  clipboard restored"
}

if ($OutFile) {
    "`n=== verification: reading $OutFile ==="
    if (Test-Path $OutFile) {
        $body = Get-Content $OutFile -Raw
        if ($body -match "INJECTED-BY-CCS-POC") { "  CONFIRMED -- injected text reached the file" }
        else { "  file exists but holds no marker" }
        ($body -split "`r?`n") | Select-Object -First 4 | ForEach-Object { "    $_" }
    } else { "  no file yet (Ctrl+S may not have fired, or the target differs)" }
}
