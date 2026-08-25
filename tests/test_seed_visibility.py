"""Seed visibility and the per-box decision record (issue #27).

A seeded file is the box's own; status must stop OVERCLAIMING ("your live
config and the checkout match") while a seeded file differs by a thousand
lines, and the question "yours or the payload's?" must be asked once,
recorded, and re-asked only when the upstream seed actually changes.

Fixture note: history stores LF, live Windows files are CRLF; the
untouched-old test writes the live file with CRLF on purpose -- the
normalization these tests pin was measured as mandatory (raw hashes match
zero history versions; tests/one-offs/poc_seed_ancestry_probe.py).
"""
from __future__ import annotations

import json
import subprocess as sp
from pathlib import Path

import pytest

from dazzle_claude_config import seeddecisions
from dazzle_claude_config.cli import main

from conftest import GIT_ID

SEED_V1 = b"# big old monolith\nrule one\nrule two\n"
SEED_V2 = b"# pointer stub\n@import layers\n"
CUSTOM = b"# my own content entirely\nmine\n"

MANIFEST = {
    "manifest_version": 1,
    "territories": {"dotclaude": {"root_var": "CLAUDE_DIR", "repo_dir": "dotclaude"}},
    "entries": [
        {"repo": "dotclaude/CLAUDE.md", "territory": "dotclaude",
         "target": "CLAUDE.md", "strategy": "seed-if-absent"},
        {"repo": "dotclaude/F.md", "territory": "dotclaude",
         "target": "F.md", "strategy": "copy"},
    ],
}


def _git(cwd: Path, *args: str) -> str:
    r = sp.run(["git", *GIT_ID, "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout


@pytest.fixture
def world(tmp_path):
    """A checkout whose seed has history (v1 then v2), plus live/user dirs."""
    co = tmp_path / "co"
    sp.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    (co / "dotclaude").mkdir()
    (co / "ccs-manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (co / "dotclaude" / "CLAUDE.md").write_bytes(SEED_V1)
    (co / "dotclaude" / "F.md").write_bytes(b"same\n")
    _git(co, "add", "-A"); _git(co, "commit", "-qm", "seed v1")
    (co / "dotclaude" / "CLAUDE.md").write_bytes(SEED_V2)
    _git(co, "commit", "-qam", "seed v2: the restructure")
    live = tmp_path / "live"; live.mkdir()
    (live / "F.md").write_bytes(b"same\n")
    user = tmp_path / "user"; user.mkdir()
    return dict(co=co, live=live, user=user)


def _ccs(w, *verb) -> list[str]:
    return ["--checkout-dir", str(w["co"]), "--claude-dir", str(w["live"]),
            "--user-claude", str(w["user"]), "--no-color", "--no-fetch", *verb]


def _status(w, *extra, capsys=None):
    rc = main(_ccs(w, "status", *extra))
    return rc, capsys.readouterr().out


# -- the honest verdict -------------------------------------------------------

def test_clean_verdict_acknowledges_uncompared_seeds(world, capsys):
    (world["live"] / "CLAUDE.md").write_bytes(CUSTOM)
    rc, out = _status(world, capsys=capsys)
    assert "everything ccs syncs matches" in out
    assert "1 seeded file is yours and not compared" in out
    assert rc == 0                          # ownership contract: not drift


def test_matching_seed_keeps_the_plain_clean_line(world, capsys):
    (world["live"] / "CLAUDE.md").write_bytes(SEED_V2)
    rc, out = _status(world, capsys=capsys)
    assert "not compared" not in out
    assert rc == 0


# -- the state machine --------------------------------------------------------

def test_untouched_old_seed_auto_offers_reseed(world, capsys):
    # The aktuldjr case, CRLF included: live is v1 with CRLF line endings,
    # history stores LF. No question -- the box never edited it.
    (world["live"] / "CLAUDE.md").write_bytes(SEED_V1.replace(b"\n", b"\r\n"))
    rc, out = _status(world, capsys=capsys)
    assert "an older seed, unmodified" in out
    assert "ccs apply --reseed CLAUDE.md" in out
    assert "yours or the payload's?" not in out


def test_customized_seed_asks_the_open_question(world, capsys):
    (world["live"] / "CLAUDE.md").write_bytes(CUSTOM)
    rc, out = _status(world, capsys=capsys)
    assert "yours or the payload's?" in out
    assert "ccs seed keep CLAUDE.md" in out


def test_keep_always_silences_for_good(world, capsys):
    (world["live"] / "CLAUDE.md").write_bytes(CUSTOM)
    assert main(_ccs(world, "seed", "keep", "CLAUDE.md", "--always")) == 0
    capsys.readouterr()
    rc, out = _status(world, capsys=capsys)
    assert "yours or the payload's?" not in out
    assert "not compared" in out            # the verdict clause still counts it
    rc, out = _status(world, "--long", capsys=capsys)
    assert "yours (kept, always)" in out


def test_keep_until_changed_reopens_when_the_seed_moves(world, capsys):
    (world["live"] / "CLAUDE.md").write_bytes(CUSTOM)
    assert main(_ccs(world, "seed", "keep", "CLAUDE.md")) == 0   # default mode
    capsys.readouterr()
    rc, out = _status(world, capsys=capsys)
    assert "yours or the payload's?" not in out                  # quiet now
    (world["co"] / "dotclaude" / "CLAUDE.md").write_bytes(b"# v3\n")
    _git(world["co"], "commit", "-qam", "seed v3")
    rc, out = _status(world, capsys=capsys)
    assert "the seed changed since you chose to keep yours" in out


def test_seed_reset_reopens_the_question(world, capsys):
    (world["live"] / "CLAUDE.md").write_bytes(CUSTOM)
    main(_ccs(world, "seed", "keep", "CLAUDE.md", "--always"))
    main(_ccs(world, "seed", "reset", "CLAUDE.md"))
    capsys.readouterr()
    rc, out = _status(world, capsys=capsys)
    assert "yours or the payload's?" in out


# -- the seed verb's edges ----------------------------------------------------

def test_seed_keep_refuses_a_non_seed_target(world, capsys):
    rc = main(_ccs(world, "seed", "keep", "F.md"))
    assert rc == 2
    assert "not a seed entry" in capsys.readouterr().err


def test_seed_list_shows_states(world, capsys):
    (world["live"] / "CLAUDE.md").write_bytes(CUSTOM)
    rc = main(_ccs(world, "seed", "list"))
    out = capsys.readouterr().out
    assert rc == 0 and "CLAUDE.md" in out


def test_malformed_decisions_file_warns_and_narrows(world, capsys):
    (world["user"] / "ccs-seed-decisions.json").write_text("{broken",
                                                          encoding="utf-8")
    (world["live"] / "CLAUDE.md").write_bytes(CUSTOM)
    rc, out = _status(world, capsys=capsys)
    assert "not valid JSON" in out
    assert "yours or the payload's?" in out   # narrowed to no-decisions, asks


def test_decisions_survive_a_round_trip(world):
    seeddecisions.keep("CLAUDE.md", "until-changed", "abc", world["user"])
    dec = seeddecisions.load(world["user"])
    assert dec.by_target["CLAUDE.md"]["seed_blob"] == "abc"
    assert seeddecisions.reset("CLAUDE.md", world["user"]) is True
    assert seeddecisions.load(world["user"]).by_target == {}


def test_record_with_wrong_decision_field_is_flagged_malformed(world):
    # Mutation-sweep closure (M5): a record whose "decision" is not "keep"
    # must be REPORTED, never silently honoured as if it were a keep.
    (world["user"] / "ccs-seed-decisions.json").write_text(
        json.dumps({"version": 1, "decisions": {"CLAUDE.md": {
            "decision": "nope", "mode": "always", "seed_blob": "x"}}}),
        encoding="utf-8")
    dec = seeddecisions.load(world["user"])
    assert dec.by_target == {}
    assert any("malformed" in e for e in dec.errors)


def test_keep_merges_with_existing_decisions(world):
    # Mutation-sweep closure (M6): recording one decision must never erase
    # another -- the file is a ledger, not a scratchpad.
    seeddecisions.keep("A.md", "always", "aaa", world["user"])
    seeddecisions.keep("B.md", "until-changed", "bbb", world["user"])
    dec = seeddecisions.load(world["user"])
    assert set(dec.by_target) == {"A.md", "B.md"}


# -- the covered-target fallback rule -----------------------------------------

def test_copy_covered_target_is_not_questioned(world, capsys):
    # A tag-free copy entry for the SAME target as the seed: the copy
    # governs; the seed fallback must stay silent about that file.
    m = json.loads((world["co"] / "ccs-manifest.json").read_text())
    m["entries"].append({"repo": "dotclaude/CL2.md", "territory": "dotclaude",
                         "target": "CLAUDE.md", "strategy": "copy"})
    (world["co"] / "ccs-manifest.json").write_text(json.dumps(m), encoding="utf-8")
    (world["co"] / "dotclaude" / "CL2.md").write_bytes(CUSTOM)
    _git(world["co"], "add", "-A"); _git(world["co"], "commit", "-qam", "cover")
    (world["live"] / "CLAUDE.md").write_bytes(CUSTOM)
    rc, out = _status(world, capsys=capsys)
    assert "yours or the payload's?" not in out
