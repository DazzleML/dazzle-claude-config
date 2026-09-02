"""i2 -- the injection driver's Python side, against a fake PowerShell.

The script itself drives a GUI and is exercised by hand (p1, and the human
checklist); what can be pinned here is the contract around it: the arguments
a profile turns into, the one-JSON-line protocol, and that every failure --
no PowerShell, no script, a timeout, junk output, a non-zero exit -- comes
back as `ok: False` with a reason and never as an exception.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from dazzle_claude_config import inject, merge


def _fake(stdout: str = "", rc: int = 0, stderr: str = ""):
    """A runner that records the command and returns canned output."""
    calls: list[list[str]] = []
    def runner(cmd, **kw):
        calls.append(list(cmd))
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=rc)
    runner.calls = calls
    return runner


@pytest.fixture
def windows(monkeypatch, tmp_path):
    """Pretend to be a Windows box with PowerShell and the shipped script."""
    monkeypatch.setattr(inject.sys, "platform", "win32")
    monkeypatch.setattr(inject, "powershell_exe", lambda: "pwsh")
    script = tmp_path / inject.DRIVER_FILE
    script.write_text("# fake", encoding="utf-8")
    monkeypatch.setattr(inject, "driver_path", lambda: script)
    return script


# --- it ships, and it is reachable the way an install reaches it

def test_the_script_ships_as_package_data():
    p = inject.driver_path()
    assert p is not None and p.name == inject.DRIVER_FILE and p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "GetFocus" in text and "SetForegroundWindow" in text, "the gate and the restore"
    assert "Clipboard]::Clear()" in text, "an empty clipboard is restored by clearing it"


def test_pyproject_and_the_wheel_check_name_the_script():
    repo = Path(__file__).resolve().parents[1]
    assert inject.DRIVER_FILE in (repo / "pyproject.toml").read_text(encoding="utf-8")
    assert inject.DRIVER_FILE in (repo / "scripts" / "check-wheel-data.py").read_text(encoding="utf-8")


# --- availability is a named refusal, never an exception

def test_not_windows_is_a_reason(monkeypatch):
    monkeypatch.setattr(inject.sys, "platform", "linux")
    ok, why = inject.available()
    assert ok is False and "Windows-only" in why
    assert inject.run_driver(["-Mode", "snapshot"])["reason"] == why


def test_no_powershell_is_a_reason(monkeypatch):
    monkeypatch.setattr(inject.sys, "platform", "win32")
    monkeypatch.setattr(inject, "powershell_exe", lambda: None)
    ok, why = inject.available()
    assert ok is False and "PowerShell" in why


def test_a_missing_script_is_a_reason(monkeypatch):
    monkeypatch.setattr(inject.sys, "platform", "win32")
    monkeypatch.setattr(inject, "powershell_exe", lambda: "pwsh")
    monkeypatch.setattr(inject, "driver_path", lambda: None)
    ok, why = inject.available()
    assert ok is False and "did not ship" in why


# --- the one-line protocol

def test_the_last_json_object_line_is_the_result(windows):
    r = _fake('WARNING: something\n{"ok": true, "tops": ["0x1"], "open": true, "children": ["0x2"]}\n')
    out = inject.snapshot("OUTPUT.md", merge.INJECT_PROFILES["bc5"], runner=r)
    assert out["ok"] is True and out["open"] is True and out["rc"] == 0


def test_no_result_line_is_a_named_failure(windows):
    r = _fake("At line:1 char:1\nUnexpected token", rc=1)
    out = inject.locate("OUTPUT.md", Path("before.json"), {}, runner=r)
    assert out["ok"] is False and "no result line" in out["reason"] and out["rc"] == 1


def test_a_refusal_keeps_its_reason_and_its_exit_code(windows):
    r = _fake('{"ok": false, "reason": "need exactly one NEW visible TTextEditor", "new": 0}', rc=2)
    out = inject.locate("OUTPUT.md", Path("before.json"), {}, runner=r)
    assert out["ok"] is False and "exactly one" in out["reason"] and out["rc"] == 2


def test_a_timeout_is_a_reason_not_an_exception(windows):
    def runner(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    out = inject.inject("OUTPUT.md", Path("b.json"), Path("p.md"), Path("o.md"), {}, runner=runner, timeout=3)
    assert out["ok"] is False and "did not finish within 3s" in out["reason"]


def test_an_unstartable_interpreter_is_a_reason(windows):
    def runner(cmd, **kw):
        raise OSError("boom")
    out = inject.snapshot("OUTPUT.md", {}, runner=runner)
    assert out["ok"] is False and "could not start PowerShell" in out["reason"]


# --- the profile becomes the script's arguments, and nothing else does

def test_the_bc5_profile_maps_onto_the_script_parameters(windows):
    r = _fake('{"ok": true}')
    inject.inject("claude-config__global.md.merged", Path("b.json"), Path("p.md"), Path("o.md"),
                  merge.INJECT_PROFILES["bc5"], runner=r)
    cmd = r.calls[0]
    assert cmd[:6] == ["pwsh", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]
    assert cmd[6].endswith(inject.DRIVER_FILE)
    tail = cmd[7:]
    for flag, value in (("-Mode", "inject"), ("-OutputName", "claude-config__global.md.merged"),
                        ("-Before", "b.json"), ("-Payload", "p.md"), ("-OutFile", "o.md"),
                        ("-TitleSuffix", " - Text Merge - Beyond Compare"),
                        ("-WindowClass", "TViewForm"), ("-PaneClass", "TTextEditor"),
                        ("-LandmarkClass", "TUiRadioButton"), ("-LandmarkText", "Other"),
                        ("-SettleMs", "400")):
        assert tail[tail.index(flag) + 1] == value, flag
    assert "-NoSave" not in tail


def test_no_save_and_a_sparse_profile(windows):
    r = _fake('{"ok": true}')
    inject.inject("x", Path("b"), Path("p"), Path("o"), {"os": "windows"}, save=False, runner=r)
    tail = r.calls[0][7:]
    assert "-NoSave" in tail
    for flag in ("-TitleSuffix", "-WindowClass", "-PaneClass", "-LandmarkClass", "-LandmarkText", "-SettleMs"):
        assert flag not in tail, f"{flag} must not be passed when the profile does not set it"


def test_ok_is_never_assumed(windows):
    """A result line without `ok` is not a success."""
    r = _fake('{"pane": "0x1"}')
    out = inject.locate("x", Path("b"), {}, runner=r)
    assert out["ok"] is False


# --- written to kill mutation survivors (v0.5.17 sweep, mode 1)

def test_the_LAST_json_line_wins_when_two_are_printed(windows):
    """M11 survived: dropping `reversed()` returned the FIRST JSON line, and
    every earlier test printed exactly one. A stray JSON-shaped warning above
    the real result must not be taken for the result."""
    r = _fake('{"ok": false, "reason": "stale first line"}\n{"ok": true, "pane": "0x2"}\n')
    out = inject.locate("x", Path("b"), {}, runner=r)
    assert out["ok"] is True and out["pane"] == "0x2"


def test_an_empty_json_object_is_a_result_not_a_missing_line(windows):
    """M12 survived: `if not result` treats `{}` as "no result line". An
    empty object IS a result line -- it becomes ok False with the exit code
    attached, and must not be described as the driver having printed nothing."""
    r = _fake("{}", rc=0)
    out = inject.snapshot("x", {}, runner=r)
    assert out == {"ok": False, "rc": 0}
    assert "no result line" not in out.get("reason", "")
