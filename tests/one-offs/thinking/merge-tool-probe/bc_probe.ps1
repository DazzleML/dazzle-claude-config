# Probe a merge tool's window tree so we can find its OUTPUT pane.
#
# Read-only: enumerates windows and reports class names and geometry. Sends
# nothing, changes nothing. The question it answers is whether the output pane
# is a standard EDIT control (WM_SETTEXT would work) or a custom-drawn one
# (only clipboard + keystrokes would).
#
#   .\bc_probe.ps1                 # list candidate tool windows
#   .\bc_probe.ps1 -Hwnd 0x1434430 # enumerate one window's children

param([string]$Hwnd = "", [string]$Match = "Beyond Compare|WinMerge|KDiff3|Meld")

Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class Win {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr h, EnumProc cb, IntPtr p);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassNameW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern int GetDlgCtrlID(IntPtr h);
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
  public static string Txt(IntPtr h){ var s=new StringBuilder(1024); GetWindowTextW(h,s,1024); return s.ToString(); }
  public static string Cls(IntPtr h){ var s=new StringBuilder(256); GetClassNameW(h,s,256); return s.ToString(); }
}
"@ -ErrorAction SilentlyContinue

function Rect($h) { $r = New-Object Win+RECT; [void][Win]::GetWindowRect($h, [ref]$r); $r }

if (-not $Hwnd) {
    "=== candidate tool windows ==="
    $found = @()
    [void][Win]::EnumWindows({ param($h,$p)
        $t = [Win]::Txt($h)
        if ($t -match $Match -and [Win]::IsWindowVisible($h)) { $script:found += $h }
        return $true }, [IntPtr]::Zero)
    foreach ($h in $found) {
        $r = Rect $h
        "  hwnd=0x{0:X}  class={1,-20} {2}x{3}  title={4}" -f `
            $h.ToInt64(), [Win]::Cls($h), ($r.R-$r.L), ($r.B-$r.T), [Win]::Txt($h)
    }
    if (-not $found) { "  none found" }
    "`nRe-run with -Hwnd <0x...> to enumerate one window's children."
    return
}

$parent = [IntPtr][Convert]::ToInt64($Hwnd, 16)
$pr = Rect $parent
"=== parent 0x{0:X}  class={1}  rect L={2} T={3} R={4} B={5} ===" -f `
    $parent.ToInt64(), [Win]::Cls($parent), $pr.L, $pr.T, $pr.R, $pr.B
""

$rows = @()
[void][Win]::EnumChildWindows($parent, { param($c,$p)
    $r = Rect $c
    $script:rows += [pscustomobject]@{
        hwnd  = "0x{0:X}" -f $c.ToInt64()
        cls   = [Win]::Cls($c)
        id    = [Win]::GetDlgCtrlID($c)
        top   = $r.T; bottom = $r.B
        w     = $r.R - $r.L; h = $r.B - $r.T
        vis   = [Win]::IsWindowVisible($c)
        txt   = (([Win]::Txt($c)) -replace "`r|`n", " ")
    }
    return $true }, [IntPtr]::Zero)

"=== {0} child controls, top to bottom ===" -f $rows.Count
$rows | Sort-Object top | ForEach-Object {
    "  {0,-12} {1,-26} id={2,-6} top={3,-6} h={4,-5} w={5,-6} vis={6,-6} {7}" -f `
        $_.hwnd, $_.cls, $_.id, $_.top, $_.h, $_.w, $_.vis,
        $_.txt.Substring(0, [Math]::Min(46, $_.txt.Length))
}

""
"=== standard text controls (WM_SETTEXT would work on these) ==="
$std = $rows | Where-Object { $_.cls -match '^(Edit|RichEdit|RICHEDIT|Scintilla)' }
if ($std) { $std | ForEach-Object { "  {0}  {1}" -f $_.hwnd, $_.cls } }
else { "  NONE -- every control is custom-drawn; WM_SETTEXT will not reach the text" }
