# Capture one window (or a control) to PNG, so a claim about what a custom
# control DISPLAYS can be checked by looking instead of by asserting.
param([string]$Hwnd = "0x1434430", [string]$Out = "")

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Shot {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
}
"@ -ErrorAction SilentlyContinue

$h = [IntPtr][Convert]::ToInt64($Hwnd, 16)
$r = New-Object Shot+RECT
[void][Shot]::GetWindowRect($h, [ref]$r)
$w = $r.R - $r.L; $ht = $r.B - $r.T
if ($w -le 0 -or $ht -le 0) { "  bad rect for $Hwnd"; return }

if (-not $Out) { $Out = "$env:TEMP\claude\bc-shot.png" }
New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null

$bmp = New-Object System.Drawing.Bitmap $w, $ht
$g   = [System.Drawing.Graphics]::FromImage($bmp)
$dc  = $g.GetHdc()
# PW_RENDERFULLCONTENT (2) captures composited/DirectComposition surfaces that
# a plain BitBlt of an occluded window would miss.
[void][Shot]::PrintWindow($h, $dc, 2)
$g.ReleaseHdc($dc); $g.Dispose()
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
"  captured ${w}x${ht} -> $Out"
