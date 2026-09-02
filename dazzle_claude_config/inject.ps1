# inject.ps1 -- the ccs injection driver (shipped as package data).
#
# Paints a file's content into a merge tool's OUTPUT pane after the tool has
# launched, for tools that regenerate that pane from the three inputs and
# discard what was on disk (BeyondCompare, measured). Three modes, one JSON
# result line on stdout, always -- the Python side (inject.py) reads only that.
#
#   snapshot  enumerate the tool's windows and child controls NOW (before the
#             launch); also answers "is a session for this file already open?"
#   locate    after the launch: the ONE visible pane of $PaneClass nearest
#             below the visible landmark that NEWLY appeared since the
#             snapshot -- exactly one, else refuse (rule confirmed by p1)
#   inject    locate, then: save foreground + clipboard -> focus the pane ->
#             GATE: GetFocus() must equal the resolved pane handle (class
#             equality is NOT enough: every BC pane is TTextEditor) ->
#             Ctrl+A, Ctrl+V, Ctrl+S -> restore -> read the output file back
#             and compare hashes. Nothing is sent unless the gate passes.
#
# Descends from tests/one-offs/thinking/merge-tool-probe/p1_locate.ps1 and
# bc_inject.ps1 -- copied, then extended; not retyped. Windows only.
#
# Exit codes: 0 done, 2 refused (reason in the JSON), 3 PowerShell language
# mode forbids Add-Type, 4 bad arguments.

param(
    [Parameter(Mandatory=$true)][ValidateSet("snapshot","locate","inject")][string]$Mode,
    [string]$OutputName = "",
    [string]$TitleSuffix = " - Text Merge - Beyond Compare",
    [string]$WindowClass = "TViewForm",
    [string]$PaneClass = "TTextEditor",
    [string]$LandmarkClass = "TUiRadioButton",
    [string]$LandmarkText = "Other",
    [string]$Before = "",
    [string]$Payload = "",
    [string]$OutFile = "",
    [int]$SettleMs = 400,
    [switch]$NoSave
)

function Emit($h, [int]$code) {
    $h["mode"] = $Mode
    Write-Output (($h | ConvertTo-Json -Compress -Depth 5))
    exit $code
}

if ($ExecutionContext.SessionState.LanguageMode -ne "FullLanguage") {
    Emit @{ ok = $false; reason = "PowerShell language mode is $($ExecutionContext.SessionState.LanguageMode); Add-Type is unavailable" } 3
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class CcsInj {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr h, EnumProc cb, IntPtr p);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassNameW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern IntPtr SetFocus(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetFocus();
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr pid);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool attach);
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
  public static string Txt(IntPtr h){ var s=new StringBuilder(1024); GetWindowTextW(h,s,1024); return s.ToString(); }
  public static string Cls(IntPtr h){ var s=new StringBuilder(256); GetClassNameW(h,s,256); return s.ToString(); }
}
"@ -ErrorAction SilentlyContinue

function Hex($h) { "0x{0:X}" -f ([int64]$h) }
function Rect($h) { $r = New-Object CcsInj+RECT; [void][CcsInj]::GetWindowRect($h, [ref]$r); $r }

# Top-level windows of the tool that show THIS output file.
#
# SCOPING, learned from the first wired run: the Enum* callbacks run as
# delegates and can only write SCRIPT-scope variables. p1_locate.ps1 lived at
# top level, where `$found` IS script scope; inside a function a local
# `$found = @()` is a different variable, the callback's `$script:found +=`
# never reaches it, and the function returns its own empty array -- "found 0
# windows" while the window sits on screen. Both accumulators are therefore
# script-scoped explicitly, reset per call.
function Find-Tops {
    $script:found = @()
    [void][CcsInj]::EnumWindows({ param($h,$p)
        if (-not [CcsInj]::IsWindowVisible($h)) { return $true }
        if ([CcsInj]::Cls($h) -ne $WindowClass) { return $true }
        $t = [CcsInj]::Txt($h)
        if ($TitleSuffix -and -not $t.EndsWith($TitleSuffix)) { return $true }
        if ($OutputName -and -not $t.StartsWith($OutputName)) { return $true }
        $script:found += $h
        return $true }, [IntPtr]::Zero)
    return ,$script:found
}

function Get-Children($top) {
    $script:rows = @()
    [void][CcsInj]::EnumChildWindows($top, { param($c,$p)
        $r = Rect $c
        $script:rows += [pscustomobject]@{
            hwnd = (Hex $c); cls = [CcsInj]::Cls($c); vis = [CcsInj]::IsWindowVisible($c)
            top = $r.T; left = $r.L; w = ($r.R - $r.L); h = ($r.B - $r.T)
            txt = (([CcsInj]::Txt($c)) -replace "`r|`n", " ")
        }
        return $true }, [IntPtr]::Zero)
    return ,$script:rows
}

# The candidate rule: visible panes of $PaneClass nearest-below a visible
# landmark; NEW = absent from the before-snapshot.
function Get-Candidates($rows, $beforeSet) {
    $landmarks = @($rows | Where-Object { $_.cls -eq $LandmarkClass -and $_.vis -and $_.txt -match [regex]::Escape($LandmarkText) })
    $panes = @($rows | Where-Object { $_.cls -eq $PaneClass })
    $cands = @()
    foreach ($lm in $landmarks) {
        $below = @($panes | Where-Object { $_.vis -and $_.top -ge $lm.top } | Sort-Object top)
        if ($below.Count -gt 0) { $cands += $below[0] }
    }
    $cands = @($cands | Sort-Object hwnd -Unique)
    $new = @($cands | Where-Object { -not $beforeSet.ContainsKey($_.hwnd) })
    return @{ landmarks = $landmarks.Count; panes = $panes.Count;
              visible = @($panes | Where-Object { $_.vis }).Count;
              candidates = $cands.Count; new = $new.Count; pick = $(if ($new.Count -eq 1) { $new[0] } else { $null }) }
}

function Read-Before {
    $set = @{}
    if ($Before -and (Test-Path $Before)) {
        $b = Get-Content -Raw $Before | ConvertFrom-Json
        foreach ($h in @($b.children)) { $set[[string]$h] = $true }
    }
    return $set
}

# ---------------------------------------------------------------- snapshot
if ($Mode -eq "snapshot") {
    $tops = Find-Tops
    $children = @()
    foreach ($t in $tops) { $children += @((Get-Children $t) | ForEach-Object { $_.hwnd }) }
    Emit @{ ok = $true; tops = @($tops | ForEach-Object { Hex $_ }); open = ($tops.Count -gt 0); children = $children } 0
}

# ---------------------------------------------------------------- locate
$tops = Find-Tops
if ($tops.Count -ne 1) {
    Emit @{ ok = $false; reason = "expected exactly one $WindowClass window for '$OutputName', found $($tops.Count)"; tops = @($tops | ForEach-Object { Hex $_ }) } 2
}
$top = $tops[0]
$rows = Get-Children $top
$c = Get-Candidates $rows (Read-Before)
if ($null -eq $c.pick) {
    Emit @{ ok = $false; reason = "need exactly one NEW visible $PaneClass below the landmark; landmarks=$($c.landmarks) panes=$($c.panes) visible=$($c.visible) candidates=$($c.candidates) new=$($c.new)"; top = (Hex $top) } 2
}
$pane = [IntPtr][Convert]::ToInt64($c.pick.hwnd, 16)
if ($Mode -eq "locate") {
    Emit @{ ok = $true; top = (Hex $top); pane = $c.pick.hwnd; candidates = $c.candidates; new = $c.new } 0
}

# ---------------------------------------------------------------- inject
if (-not $Payload -or -not (Test-Path $Payload)) { Emit @{ ok = $false; reason = "-Payload file not found: $Payload" } 4 }
$payloadText = [IO.File]::ReadAllText($Payload)
$payloadHash = (Get-FileHash -Algorithm SHA256 $Payload).Hash

$fg = [CcsInj]::GetForegroundWindow()
$saved = $null; $hadClip = $false
try { $saved = Get-Clipboard -Raw -ErrorAction SilentlyContinue; $hadClip = ($null -ne $saved) } catch {}

$result = @{ ok = $false; top = (Hex $top); pane = $c.pick.hwnd; focus_verified = $false; sent = $false; saved = $false; payload_sha256 = $payloadHash }
try {
    [void][CcsInj]::SetForegroundWindow($top)
    Start-Sleep -Milliseconds $SettleMs
    $tid = [CcsInj]::GetWindowThreadProcessId($top, [IntPtr]::Zero)
    $me  = [CcsInj]::GetCurrentThreadId()
    [void][CcsInj]::AttachThreadInput($me, $tid, $true)
    [void][CcsInj]::SetFocus($pane)
    $focused = [CcsInj]::GetFocus()
    [void][CcsInj]::AttachThreadInput($me, $tid, $false)
    $result["focused"] = (Hex $focused)
    $result["focused_class"] = [CcsInj]::Cls($focused)
    # THE GATE: handle equality. Class equality would pass with focus on the
    # LEFT pane (also a TTextEditor) and paste over the person's live side.
    if ([int64]$focused -ne [int64]$pane) {
        $result["reason"] = "focus landed on $(Hex $focused) ($([CcsInj]::Cls($focused))), wanted $($c.pick.hwnd) -- nothing sent"
        Emit $result 2
    }
    $result["focus_verified"] = $true

    Set-Clipboard -Value $payloadText
    Start-Sleep -Milliseconds 150
    [System.Windows.Forms.SendKeys]::SendWait("^a")
    Start-Sleep -Milliseconds 150
    [System.Windows.Forms.SendKeys]::SendWait("^v")
    Start-Sleep -Milliseconds $SettleMs
    $result["sent"] = $true
    if (-not $NoSave) {
        [System.Windows.Forms.SendKeys]::SendWait("^s")
        Start-Sleep -Milliseconds 1000
        $result["saved"] = $true
    }
}
finally {
    # Restore the clipboard: an EMPTY clipboard is restored by clearing it.
    try { if ($hadClip) { Set-Clipboard -Value $saved } else { [System.Windows.Forms.Clipboard]::Clear() } } catch {}
    if ($fg -ne [IntPtr]::Zero) { [void][CcsInj]::SetForegroundWindow($fg) }
}

# Verify by reading the artifact, never by trusting the send.
if ($OutFile -and (Test-Path $OutFile)) {
    $back = (Get-FileHash -Algorithm SHA256 $OutFile).Hash
    $result["readback_sha256"] = $back
    if ($back -eq $payloadHash) {
        $result["verified"] = "exact"; $result["ok"] = $true
    } else {
        $norm = { param($s) $s -replace "`r`n", "`n" }
        $a = & $norm ([IO.File]::ReadAllText($OutFile)); $b = & $norm $payloadText
        if ($a -eq $b) { $result["verified"] = "equal-modulo-eol"; $result["ok"] = $true }
        else { $result["verified"] = "mismatch"; $result["reason"] = "the saved file does not hold the injected bytes" }
    }
} else {
    $result["verified"] = "unread"; $result["reason"] = "no -OutFile to read back, or it does not exist"
}
Emit $result $(if ($result["ok"]) { 0 } else { 2 })
