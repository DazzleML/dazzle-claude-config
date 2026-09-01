# p1 instrument: snapshot a BC window's child tree, or compare the live tree
# against a snapshot and print the injection CANDIDATES with a cardinality
# verdict. Read-only: enumerates and reads; never focuses, clicks, or sends.
#
#   .\p1_locate.ps1 -Hwnd 0x1434430 -Snapshot before.json
#   .\p1_locate.ps1 -Hwnd 0x1434430 -Compare  before.json
#
# Candidate rule under test (design S-B, corrected 2026-09-01): a VISIBLE pane
# of $PaneClass whose top edge lies below the landmark ($LandmarkClass with
# text $LandmarkText) and is the nearest such pane -- "exactly one, else refuse".
# NEW = its hwnd was absent from the snapshot. Z-order rank is recorded as
# evidence (walked via GetWindow GW_HWNDPREV), never used to pick.
#
# Descends from bc_probe.ps1 (copied, then extended -- not retyped).

param(
    [string]$Hwnd,
    [string]$Snapshot = "",
    [string]$Compare = "",
    [string]$PaneClass = "TTextEditor",
    [string]$LandmarkClass = "TUiRadioButton",
    [string]$LandmarkText = "Other"
)

Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class P1 {
  [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr h, EnumProc cb, IntPtr p);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassNameW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h, uint cmd);
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
  public static string Txt(IntPtr h){ var s=new StringBuilder(1024); GetWindowTextW(h,s,1024); return s.ToString(); }
  public static string Cls(IntPtr h){ var s=new StringBuilder(256); GetClassNameW(h,s,256); return s.ToString(); }
  public const uint GW_HWNDPREV = 3;
}
"@ -ErrorAction SilentlyContinue

if (-not $Hwnd) { "need -Hwnd <0x...> (find it with bc_probe.ps1)"; exit 2 }
$parent = [IntPtr][Convert]::ToInt64($Hwnd, 16)

function Rect($h) { $r = New-Object P1+RECT; [void][P1]::GetWindowRect($h, [ref]$r); $r }
function ZRank($h) {
    # number of windows above it in its sibling chain: 0 = topmost sibling
    $n = 0; $p = [P1]::GetWindow($h, [P1]::GW_HWNDPREV)
    while ($p -ne [IntPtr]::Zero -and $n -lt 10000) { $n++; $p = [P1]::GetWindow($p, [P1]::GW_HWNDPREV) }
    $n
}

$rows = @()
[void][P1]::EnumChildWindows($parent, { param($c,$p)
    $r = Rect $c
    $script:rows += [pscustomobject]@{
        hwnd = ("0x{0:X}" -f $c.ToInt64()); cls = [P1]::Cls($c)
        vis = [P1]::IsWindowVisible($c); z = (ZRank $c)
        top = $r.T; left = $r.L; w = ($r.R - $r.L); h = ($r.B - $r.T)
        txt = (([P1]::Txt($c)) -replace "`r|`n", " ")
    }
    return $true }, [IntPtr]::Zero)

if ($Snapshot) {
    $rows | ConvertTo-Json -Depth 3 | Set-Content -Encoding UTF8 $Snapshot
    "snapshot: {0} children -> {1}" -f $rows.Count, $Snapshot
    exit 0
}

$before = @{}
if ($Compare) {
    foreach ($b in (Get-Content -Raw $Compare | ConvertFrom-Json)) { $before[$b.hwnd] = $true }
    "compared against {0} ({1} children then, {2} now)" -f $Compare, $before.Count, $rows.Count
}

# landmark: visible radio button with the given text (there may be one per session; take visible only)
$landmarks = @($rows | Where-Object { $_.cls -eq $LandmarkClass -and $_.vis -and $_.txt -match $LandmarkText })
"landmarks ($LandmarkClass '$LandmarkText', visible): {0}" -f $landmarks.Count
foreach ($lm in $landmarks) { "  {0}  top={1} z={2}" -f $lm.hwnd, $lm.top, $lm.z }

$panes = @($rows | Where-Object { $_.cls -eq $PaneClass })
"panes ($PaneClass): {0} total, {1} visible, {2} hidden" -f $panes.Count,
    @($panes | Where-Object { $_.vis }).Count, @($panes | Where-Object { -not $_.vis }).Count
foreach ($pn in ($panes | Sort-Object top)) {
    $flag = if ($Compare) { if ($before.ContainsKey($pn.hwnd)) { "EXISTING" } else { "NEW" } } else { "-" }
    "  {0}  vis={1,-5} z={2,-4} top={3,-5} h={4,-5} {5}" -f $pn.hwnd, $pn.vis, $pn.z, $pn.top, $pn.h, $flag
}

# candidates: visible panes below a visible landmark; nearest below each landmark
$cands = @()
foreach ($lm in $landmarks) {
    $below = @($panes | Where-Object { $_.vis -and $_.top -ge $lm.top } | Sort-Object top)
    if ($below.Count -gt 0) { $cands += $below[0] }
}
$cands = @($cands | Sort-Object hwnd -Unique)
$newc = @($cands | Where-Object { -not $Compare -or -not $before.ContainsKey($_.hwnd) })

""
"CANDIDATES (visible {0} nearest-below a visible landmark): {1}" -f $PaneClass, $cands.Count
foreach ($c in $cands) {
    $flag = if ($Compare) { if ($before.ContainsKey($c.hwnd)) { "EXISTING" } else { "NEW" } } else { "-" }
    "  {0}  z={1} top={2}  {3}" -f $c.hwnd, $c.z, $c.top, $flag
}
if ($Compare) {
    "VERDICT (newly-appeared rule): {0}" -f $(if ($newc.Count -eq 1) { "ACCEPT " + $newc[0].hwnd } else { "REFUSE -- $($newc.Count) new candidate(s), need exactly 1" })
}
"VERDICT (visible-cardinality rule): {0}" -f $(if ($cands.Count -eq 1) { "ACCEPT " + $cands[0].hwnd } else { "REFUSE -- $($cands.Count) visible candidate(s), need exactly 1" })
