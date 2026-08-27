"""Direction attribution must not name a side that holds nothing unique (#36).

The base is ESTIMATED (`merge.infer_base`), and an estimate that names a side
as "ahead" is checkable against a fact computed for the same file: how many
lines each side holds that the other does not.

Measured shape of the defect: a live file that is merely STALE -- nothing
edited it, a checkout-side change was committed and never applied -- came back
labelled `live ahead` with `0 only live` printed on the same line, and `apply`
told the user to run `ccs collect`, which reverts the committed work. The
estimate and the counts disagreed, and the counts were right.

These are the scenarios from `tests/one-offs/thinking/poc_attribution_inversion_sweep.py`,
promoted to a suite so a future change that re-inverts a previously-correct row
fails here rather than in someone's live config.
"""
from __future__ import annotations

import json
import subprocess as sp
from pathlib import Path

import pytest

from dazzle_claude_config.cli import main

from conftest import GIT_ID

MANIFEST = {
    "manifest_version": 1,
    "territories": {"dotclaude": {"root_var": "CLAUDE_DIR", "repo_dir": "dotclaude"}},
    "entries": [{"repo": "dotclaude/skills", "territory": "dotclaude",
                 "target": "skills", "strategy": "copy"}],
}


def _git(cwd: Path, *args: str) -> None:
    r = sp.run(["git", *GIT_ID, "-C", str(cwd), *args],
               capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"


def _lines(*ls: str) -> bytes:
    return "".join(l + "\n" for l in ls).encode()


V1 = _lines("# skill", "rule A")
V2 = _lines("# skill", "rule A", "rule B")
V3 = _lines("# skill", "rule A", "rule B", "rule C", "rule D", "rule E")


def _world(tmp_path: Path, history: list[bytes], live_content: bytes) -> dict:
    co, live, user = tmp_path / "co", tmp_path / "live", tmp_path / "user"
    (co / "dotclaude" / "skills").mkdir(parents=True)
    (live / "skills").mkdir(parents=True)
    user.mkdir()
    (co / "ccs-manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    sp.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    target = co / "dotclaude" / "skills" / "s.md"
    for i, content in enumerate(history):
        target.write_bytes(content)
        if i == 0:
            _git(co, "add", "-A")
            _git(co, "commit", "-qm", "v1")
        else:
            _git(co, "commit", "-qam", f"v{i + 1}")
    (live / "skills" / "s.md").write_bytes(live_content)
    return dict(co=co, live=live, user=user)


def _ccs(w: dict, *verb: str) -> list[str]:
    return ["--checkout-dir", str(w["co"]), "--claude-dir", str(w["live"]),
            "--user-claude", str(w["user"]), "--no-color", "--no-fetch", *verb]


# -- the defect itself ---------------------------------------------------------

def test_a_stale_live_file_is_not_called_live_ahead(tmp_path, capsys):
    """#36: live lacks rule E, holds nothing unique. It cannot be ahead."""
    w = _world(tmp_path, [V1, V2, V3],
               _lines("# skill", "rule A", "rule B", "rule C", "rule D"))
    main(_ccs(w, "status", "--long"))
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if "s.md" in l and "--" in l)
    assert "0 only live" in line, line          # the fact the label must respect
    assert "live ahead" not in line, line       # ...and the label that ignored it
    assert "unattributed" in line, line


def test_apply_does_not_tell_you_to_collect_a_stale_file(tmp_path, capsys):
    """The harm, not just the label: following that hint reverts committed work."""
    w = _world(tmp_path, [V1, V2, V3],
               _lines("# skill", "rule A", "rule B", "rule C", "rule D"))
    main(_ccs(w, "apply"))
    out = capsys.readouterr().out
    assert "`ccs collect` it" not in out, out
    # The correct action for a stale live file is to apply it.
    assert (w["live"] / "skills" / "s.md").read_bytes() == V3, out


def test_the_evidence_names_stale_as_the_likelier_reading(tmp_path, capsys):
    w = _world(tmp_path, [V1, V2, V3],
               _lines("# skill", "rule A", "rule B", "rule C", "rule D"))
    main(_ccs(w, "status", "--long"))
    out = capsys.readouterr().out
    assert "STALE" in out
    assert "ccs diff" in out          # points at the READ-ONLY verb


# -- the veto must not over-correct -------------------------------------------

def test_a_genuine_live_edit_is_still_called_live_ahead(tmp_path, capsys):
    """live holds a line the checkout lacks -- the label is earned."""
    w = _world(tmp_path, [V1, V2], V2 + b"local note\n")
    main(_ccs(w, "status", "--long"))
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if "s.md" in l and "--" in l)
    assert "1 only live" in line, line
    assert "live ahead" in line, line


def test_a_genuine_live_edit_still_gets_the_collect_hint(tmp_path, capsys):
    w = _world(tmp_path, [V1, V2], V2 + b"local note\n")
    main(_ccs(w, "apply"))
    out = capsys.readouterr().out
    assert "`ccs collect` it" in out, out


def test_live_matching_an_older_commit_is_still_checkout_ahead(tmp_path, capsys):
    """The proven case (rule 2) is untouched: live byte-equals a commit."""
    w = _world(tmp_path, [V1, V2], V1)
    main(_ccs(w, "status", "--long"))
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if "s.md" in l and "--" in l)
    assert "checkout ahead" in line, line


def test_a_two_sided_change_is_still_two_sided(tmp_path, capsys):
    w = _world(tmp_path, [V1, V2], V1 + b"local note\n")
    main(_ccs(w, "status", "--long"))
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if "s.md" in l and "--" in l)
    assert "two-sided" in line or "both moved" in line, line


# -- the invariant, stated once ------------------------------------------------

@pytest.mark.parametrize("history,live,forbidden", [
    ([V1, V2, V3], _lines("# skill", "rule A", "rule B", "rule C", "rule D"),
     "live ahead"),
    ([V1, V2], V1, "live ahead"),
])
def test_no_side_is_called_ahead_while_holding_zero_unique_lines(
        tmp_path, capsys, history, live, forbidden):
    """The general rule, independent of which shape produced it."""
    w = _world(tmp_path, history, live)
    main(_ccs(w, "status", "--long"))
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if "s.md" in l and "--" in l)
    if "0 only live" in line:
        assert "live ahead" not in line, line
    if "0 only checkout" in line:
        assert "checkout ahead" not in line, line


def test_the_entry_rollup_does_not_contradict_the_file_beneath_it(tmp_path, capsys):
    """The rollup counted `unattributed` as one-sided.

    `1 file differs (all one-sided)` printed directly above a file line reading
    `unattributed` is the same output making two different claims. Whatever the
    rollup says, it must not say "all one-sided" when nothing under it is.
    """
    w = _world(tmp_path, [V1, V2, V3],
               _lines("# skill", "rule A", "rule B", "rule C", "rule D"))
    main(_ccs(w, "status"))
    out = capsys.readouterr().out
    entry = next(l for l in out.splitlines() if "dotclaude/skills:" in l)
    assert "all one-sided" not in entry, entry
    assert "unproven" in entry or "unattributed" in entry, entry


def test_a_genuinely_one_sided_entry_still_rolls_up_as_one_sided(tmp_path, capsys):
    """The rollup change must not relabel the ordinary case."""
    w = _world(tmp_path, [V1, V2], V2 + b"local note\n")
    (w["live"] / "skills" / "t.md").write_bytes(b"# t\nlocal\n")
    _git(w["co"], "status")   # no-op; keeps the fixture honest about state
    main(_ccs(w, "status"))
    out = capsys.readouterr().out
    entry = next((l for l in out.splitlines() if "dotclaude/skills:" in l), "")
    assert "all one-sided" in entry or "differs" in entry, entry


def test_an_upstream_retirement_against_a_PROVEN_base_is_not_vetoed(tmp_path, capsys):
    """The veto must not fire when the base is a proof rather than an estimate.

    `base == ours` means the live file byte-EQUALS a commit (infer_base rule 2,
    "distance zero, proof not guess"), so the checkout is certainly the side
    that moved -- and a move that REMOVES lines legitimately leaves the
    checkout holding nothing unique. An earlier version of the #36 veto fired
    here too and called the most proven case in the tool "direction unproven".

    Caught by mutation J2, which survived because no test covered this branch.
    """
    w = _world(tmp_path, [V2, _lines("# skill", "rule A")], V2)   # checkout retired rule B
    main(_ccs(w, "status", "--long"))
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if "s.md" in l and "--" in l)
    assert "0 only checkout" in line, line      # the checkout holds nothing unique...
    assert "checkout ahead" in line, line       # ...and is still, correctly, ahead
    assert "unattributed" not in line, line


def test_the_retirement_still_propagates_to_live(tmp_path, capsys):
    w = _world(tmp_path, [V2, _lines("# skill", "rule A")], V2)
    main(_ccs(w, "apply"))
    assert (w["live"] / "skills" / "s.md").read_bytes() == _lines("# skill", "rule A")


def test_a_local_snap_is_not_rolled_up_as_one_sided_either(tmp_path, capsys):
    """The rollup must not absorb ANY non-one-sided kind into the green claim.

    Seen live: `1 file differs (all one-sided)` printed directly above a file
    line reading `local snap`. The first fix for this counted only the kinds
    known at the time, so a kind it did not enumerate fell straight through
    into "all one-sided" -- the same contradiction, one kind later. The rollup
    now counts what is NOT one-sided rather than listing what is.
    """
    w = _world(tmp_path, [V1, V2], V2)
    # A file present on BOTH sides but never committed -> "local snap".
    (w["co"] / "dotclaude" / "skills" / "fresh.md").write_bytes(b"# fresh\nfrom the checkout\n")
    (w["live"] / "skills" / "fresh.md").write_bytes(b"# fresh\ndiffers in live\n")
    main(_ccs(w, "status"))
    out = capsys.readouterr().out
    entry = next(l for l in out.splitlines() if "dotclaude/skills:" in l)
    assert "local snap" in out
    assert "all one-sided" not in entry, entry
