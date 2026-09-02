"""i3a-c -- the resume decision reads the tool's capability, and the injection
flow's safety order is mechanical.

Every test here stands in for the GUI with spies: `_spawn` never starts a
process, `inject.*` return canned dicts, and the "tool" is a fake that may
save over the merged file -- because that is what the real one does. The
property under test is never "the right call was made" alone; it is what the
person's `.merged` bytes are afterwards.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dazzle_claude_config import merge
from test_adoption_merge import world  # noqa: F401  -- the shared three-way world


# --- fixtures ----------------------------------------------------------------

class _Proc:
    """A launched tool: `on_exit` runs when ccs waits for it (the moment a real
    tool saves and closes)."""
    def __init__(self, rc=0, on_exit=None):
        self.rc, self.on_exit, self._polled = rc, on_exit, 0
    def poll(self):
        return None
    def wait_and_exit(self):
        if self.on_exit:
            self.on_exit()
        return self.rc


def _install_spies(monkeypatch, *, tier: str, spawn_rc: int = 0, on_exit=None,
                   snapshot=None, locate=None, inject_result=None, available=(True, "")):
    """Wire every GUI touchpoint to a recorder. Returns the recorder."""
    rec = SimpleNamespace(spawned=[], snapshots=[], locates=[], injects=[], waited=[])
    registry = {"tools": {"fake": {"resume": tier}}, "executables": {},
                "inject_profiles": {"fp": {"os": "windows" if merge.sys.platform == "win32" else "posix",
                                           "pane": {"class": "TTextEditor"}}}}
    monkeypatch.setattr(merge, "effective_registry", lambda user_claude=None: (registry, []))
    monkeypatch.setattr(merge, "resolve_tool", lambda explicit=None: "fake")
    monkeypatch.setattr(merge, "tool_command", lambda name: 'faketool "$MERGED"')
    monkeypatch.setattr(merge, "interactive", lambda: True)

    def spawn(tool, item, merged, base):
        rec.spawned.append(item.label)
        return _Proc(spawn_rc, on_exit)
    monkeypatch.setattr(merge, "_spawn", spawn)
    def wait(proc):
        rec.waited.append(proc)
        return proc.wait_and_exit()
    monkeypatch.setattr(merge, "_wait_for_tool", wait)

    monkeypatch.setattr(merge.inject, "available", lambda: available)
    monkeypatch.setattr(merge.inject, "snapshot",
                        lambda name, profile, **kw: rec.snapshots.append(name) or
                        (snapshot if snapshot is not None else {"ok": True, "open": False, "children": []}))
    monkeypatch.setattr(merge.inject, "locate",
                        lambda name, before, profile, **kw: rec.locates.append(name) or
                        (locate if locate is not None else {"ok": True, "pane": "0x1", "top": "0x2"}))
    monkeypatch.setattr(merge.inject, "inject",
                        lambda name, before, payload, out, profile, **kw: rec.injects.append((name, payload, out)) or
                        (inject_result if inject_result is not None else {"ok": True, "verified": "exact"}))
    monkeypatch.setattr(merge, "INJECT_WINDOW_WAIT_S", 2.0)
    monkeypatch.setattr(merge, "INJECT_POLL_S", 0.0)
    return rec


def _resumed_world(world):
    """Run once (seeds), edit the merged file by hand, return the pieces.
    Mirrors test_adoption_merge's resume tests."""
    manifest, co, roots, base_file = world
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(merge, "launch", lambda *a, **k: 0)
        mp.setattr(merge, "effective_registry", lambda user_claude=None: ({"tools": {}, "executables": {}, "inject_profiles": {}}, []))
        mp.setattr(merge, "resolve_tool", lambda explicit=None: "fake")
        mp.setattr(merge, "tool_command", lambda name: 'faketool "$MERGED"')
        merge.run(manifest, co, roots, only="dotclaude/CLAUDE.md",
                  base_override=base_file.read_bytes(), base_label="file:a")
    merged = merge.workspace_for(roots) / "CLAUDE.md.merged"
    text = merged.read_text(encoding="utf-8")
    lines = [l for l in text.splitlines() if not l.startswith(("<<<<<<<", "=======", ">>>>>>>", "|||||||"))]
    # write_bytes, not write_text: text mode would turn "\n" into "\r\n" on
    # Windows and the bytes the flow must preserve would differ from `kept`
    kept = ("\n".join(lines) + "\nHAND-RESOLVED-MARKER\n").encode("utf-8")
    merged.write_bytes(kept)
    return manifest, co, roots, base_file, merged, kept


def _run(world_bits, **kw):
    manifest, co, roots, base_file, merged, kept = world_bits
    return merge.run(manifest, co, roots, only="dotclaude/CLAUDE.md",
                     base_override=base_file.read_bytes(), base_label="file:a", **kw)


# --- the decision reads the capability --------------------------------------

def test_a_preloads_tool_reopens_a_resumed_file_without_relaunch(world, monkeypatch):
    bits = _resumed_world(world)
    rec = _install_spies(monkeypatch, tier="preloads")
    r = _run(bits)
    assert r.resumed and r.reopened and rec.spawned == ["CLAUDE.md"]
    assert r.tier == "preloads" and r.tool == "fake"
    assert bits[4].read_bytes() == bits[5], "reopening a preloads tool touches nothing"


def test_a_writes_only_tool_keeps_a_resumed_file_closed(world, monkeypatch):
    bits = _resumed_world(world)
    rec = _install_spies(monkeypatch, tier="writes-only")
    r = _run(bits)
    assert r.resumed and not r.reopened and rec.spawned == []


def test_an_inject_tool_keeps_a_resumed_file_closed_without_relaunch(world, monkeypatch):
    bits = _resumed_world(world)
    rec = _install_spies(monkeypatch, tier="inject:fp")
    r = _run(bits)
    assert rec.spawned == [] and not r.injected and not r.inject_refused
    assert r.tier == "inject:fp"


def test_an_inject_profile_for_another_platform_collapses_to_writes_only(monkeypatch):
    reg = {"tools": {"t": {"resume": "inject:elsewhere"}}, "executables": {},
           "inject_profiles": {"elsewhere": {"os": "posix" if merge.sys.platform == "win32" else "windows"}}}
    assert merge.tool_resume("t", reg) == "inject:elsewhere"
    assert merge.inject_profile_for("t", reg) is None
    assert merge.effective_tier("t", reg) == merge.RESUME_WRITES_ONLY
    assert merge.reopen_is_safe("t", reg) is False


# --- the injection flow, in its safety order ---------------------------------

def test_relaunch_on_an_inject_tool_paints_the_work_back_and_records_it(world, monkeypatch):
    bits = _resumed_world(world)
    merged, kept = bits[4], bits[5]
    rec = _install_spies(monkeypatch, tier="inject:fp", spawn_rc=0)
    r = _run(bits, relaunch=True, inject_mode="always")
    assert rec.snapshots == ["CLAUDE.md.merged"], "snapshot BEFORE launch"
    assert rec.spawned == ["CLAUDE.md"]
    assert rec.locates and rec.injects
    name, payload, out = rec.injects[0]
    assert payload.name == "CLAUDE.md.merged.pre-inject" and out == merged
    assert r.injected and not r.inject_failed and not r.restored
    assert r.tool_exit["CLAUDE.md"] == 0
    assert merged.read_bytes() == kept
    assert not (merged.parent / "CLAUDE.md.merged.pre-inject").exists(), "sidecar cleaned up"


def test_an_already_open_session_is_refused_before_any_launch(world, monkeypatch):
    bits = _resumed_world(world)
    rec = _install_spies(monkeypatch, tier="inject:fp",
                         snapshot={"ok": True, "open": True, "children": ["0x9"]})
    r = _run(bits, relaunch=True, inject_mode="always")
    assert rec.spawned == [], "nothing launched"
    assert r.inject_refused and "already has" in r.inject_refused[0][1]
    assert bits[4].read_bytes() == bits[5]


def test_declining_consent_launches_nothing(world, monkeypatch):
    bits = _resumed_world(world)
    rec = _install_spies(monkeypatch, tier="inject:fp")
    r = _run(bits, relaunch=True, inject_mode="ask", confirm_inject=lambda item, tool: False)
    assert rec.spawned == [] and r.inject_refused and "declined" in r.inject_refused[0][1]


def test_never_mode_refuses_with_the_setting_named(world, monkeypatch):
    bits = _resumed_world(world)
    rec = _install_spies(monkeypatch, tier="inject:fp")
    r = _run(bits, relaunch=True, inject_mode="never")
    assert rec.spawned == [] and "merge_inject" in r.inject_refused[0][1]


def test_no_driver_is_a_named_refusal_not_a_launch(world, monkeypatch):
    bits = _resumed_world(world)
    rec = _install_spies(monkeypatch, tier="inject:fp", available=(False, "the injection driver is Windows-only"))
    r = _run(bits, relaunch=True, inject_mode="always")
    assert rec.spawned == [] and "Windows-only" in r.inject_refused[0][1]


def test_a_failed_paint_that_the_tool_saved_over_is_restored_before_validation(world, monkeypatch):
    """The slot the review found: the tool regenerated the pane, the paint was
    not verified, the person saved. Between the tool exiting and validate()
    the sidecar goes back -- so a clean regenerated merge is never installed
    as the person's resolution."""
    bits = _resumed_world(world)
    merged, kept = bits[4], bits[5]
    regenerated = b"# regenerated by the tool\n"
    rec = _install_spies(monkeypatch, tier="inject:fp", spawn_rc=0,
                         inject_result={"ok": False, "reason": "focus landed elsewhere"},
                         on_exit=lambda: merged.write_bytes(regenerated))
    r = _run(bits, relaunch=True, inject_mode="always")
    assert r.inject_failed and "focus landed elsewhere" in r.inject_failed[0][1]
    assert r.restored, "the overwrite was detected"
    assert merged.read_bytes() == kept, "and the person's bytes are back"
    # validation ran on the RESTORED bytes, not the regenerated ones
    assert b"regenerated by the tool" not in merged.read_bytes()


def test_a_failed_paint_with_no_save_restores_nothing_and_says_so(world, monkeypatch):
    bits = _resumed_world(world)
    merged, kept = bits[4], bits[5]
    rec = _install_spies(monkeypatch, tier="inject:fp",
                         locate={"ok": False, "reason": "need exactly one NEW visible pane"})
    r = _run(bits, relaunch=True, inject_mode="always")
    assert r.inject_failed and "could not be located" in r.inject_failed[0][1]
    assert not r.restored and merged.read_bytes() == kept
    assert rec.injects == [], "nothing sent when the pane is not located"


def test_discard_is_the_old_destructive_reopen_and_is_named(world, monkeypatch):
    bits = _resumed_world(world)
    rec = _install_spies(monkeypatch, tier="inject:fp")
    r = _run(bits, relaunch=True, discard=True, inject_mode="always")
    assert rec.spawned == ["CLAUDE.md"] and r.discarded
    assert rec.snapshots == [] and rec.injects == [], "no injection machinery on --discard"


def test_the_tools_exit_code_is_recorded_per_item(world, monkeypatch):
    bits = _resumed_world(world)
    rec = _install_spies(monkeypatch, tier="preloads", spawn_rc=101)
    r = _run(bits)
    assert r.tool_exit == {"CLAUDE.md": 101}


def test_the_console_prompt_never_answers_yes_without_a_console(monkeypatch, capsys):
    monkeypatch.setattr(merge, "_console_attached", lambda stream: False)
    item = SimpleNamespace(label="x")
    assert merge._ask_inject_on_console(item, "bc") is False
    out = capsys.readouterr().out
    assert "take the keyboard" in out and "no console" in out
