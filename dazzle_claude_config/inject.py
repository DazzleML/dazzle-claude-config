"""The injection driver's Python side: run `inject.ps1`, read its one result line.

What this module knows: where the shipped script is, which PowerShell to run
it with, how to hand it a profile's fields, and how to turn its single JSON
line -- or its absence -- into a dict the merge flow can act on. What it does
NOT know: anything about panes, focus, or keystrokes. Every decision that can
send a keystroke lives in the script, behind its handle-equality gate; every
decision about what to do with the answer lives in merge.run().

Every failure is a dict with `ok: False` and a `reason`; nothing here raises
for a driver problem, because the caller's fallback -- leave the file closed --
is always available and must always be reachable.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

DRIVER_FILE = "inject.ps1"
#: How long the script may run. The inject mode sleeps ~2 s by design; the
#: rest is window enumeration. A tool that never shows a window must not hang
#: the merge.
DEFAULT_TIMEOUT = 45


def driver_path() -> Path | None:
    """The shipped script, or None if the package data did not ship."""
    try:
        from importlib import resources
        p = Path(str(resources.files("dazzle_claude_config").joinpath(DRIVER_FILE)))
    except (ModuleNotFoundError, TypeError, OSError):
        return None
    return p if p.is_file() else None


def powershell_exe() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def available() -> tuple[bool, str]:
    """(usable, reason) -- the platform, the interpreter, and the script."""
    if sys.platform != "win32":
        return False, "the injection driver is Windows-only"
    if not powershell_exe():
        return False, "no PowerShell on PATH (pwsh or powershell)"
    if driver_path() is None:
        return False, f"{DRIVER_FILE} did not ship with this install"
    return True, ""


def _profile_args(profile: dict) -> list[str]:
    """Map a registry inject profile onto the script's parameters. Only the
    fields the script understands; anything else in the profile is
    documentation."""
    win = profile.get("window", {}) if isinstance(profile.get("window"), dict) else {}
    pane = profile.get("pane", {}) if isinstance(profile.get("pane"), dict) else {}
    lm = pane.get("landmark", {}) if isinstance(pane.get("landmark"), dict) else {}
    args: list[str] = []
    for flag, value in (("-TitleSuffix", win.get("title_suffix")),
                        ("-WindowClass", win.get("class")),
                        ("-PaneClass", pane.get("class")),
                        ("-LandmarkClass", lm.get("class")),
                        ("-LandmarkText", lm.get("text")),
                        ("-SettleMs", profile.get("settle_ms"))):
        if value is not None and value != "":
            args += [flag, str(value)]
    return args


def run_driver(args: list[str], *, timeout: float = DEFAULT_TIMEOUT,
               runner=subprocess.run) -> dict:
    """Run the script with `args`; return its result line as a dict.

    The result is the LAST stdout line that parses as a JSON object -- the
    script prints exactly one, but a stray warning above it must not break
    the contract. Missing line, timeout, or an interpreter failure all come
    back as `ok: False` with a reason.
    """
    usable, why = available()
    if not usable:
        return {"ok": False, "reason": why}
    cmd = [powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
           "-File", str(driver_path()), *args]
    try:
        proc = runner(cmd, capture_output=True, text=True, encoding="utf-8",
                      errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": f"the injection driver did not finish within {timeout:g}s"}
    except OSError as exc:
        return {"ok": False, "reason": f"could not start PowerShell: {exc}"}
    result = _last_json_line(proc.stdout or "")
    if result is None:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [""]
        return {"ok": False, "reason": f"the injection driver printed no result line "
                                       f"(rc={proc.returncode}: {tail[0][:120]})",
                "rc": proc.returncode}
    result.setdefault("ok", False)
    result["rc"] = proc.returncode
    return result


def _last_json_line(text: str) -> dict | None:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                body = json.loads(line)
            except ValueError:
                continue
            if isinstance(body, dict):
                return body
    return None


def snapshot(output_name: str, profile: dict, **kw) -> dict:
    """Before the launch: the tool's matching windows and their child handles.
    `open` is True when a session for this file is ALREADY showing -- the
    pre-launch refusal p1 found necessary."""
    return run_driver(["-Mode", "snapshot", "-OutputName", output_name, *_profile_args(profile)], **kw)


def locate(output_name: str, before: Path, profile: dict, **kw) -> dict:
    return run_driver(["-Mode", "locate", "-OutputName", output_name, "-Before", str(before),
                       *_profile_args(profile)], **kw)


def inject(output_name: str, before: Path, payload: Path, out_file: Path,
           profile: dict, *, save: bool = True, **kw) -> dict:
    """Locate, gate, paint, save, read back. `ok` is True only when the saved
    file's bytes equal the payload's (exactly, or modulo line endings, which
    the result names as `verified`)."""
    args = ["-Mode", "inject", "-OutputName", output_name, "-Before", str(before),
            "-Payload", str(payload), "-OutFile", str(out_file), *_profile_args(profile)]
    if not save:
        args.append("-NoSave")
    return run_driver(args, **kw)
