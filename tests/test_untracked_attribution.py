"""Untracked checkout files: the authoring loop, and the summary that lied (#29).

The house workflow for building a payload file is: create it in the
checkout, `ccs apply` it live, edit the checkout copy, apply again. The
second apply used to refuse -- "checkout copy is an uncommitted local
snapshot, older than live" -- which asserts a direction git cannot
supply for a path that was never committed, and then printed "your live
config already matches the checkout" when it demonstrably did not.

The guard those two lines came from exists to stop a one-way copy from
discarding COMMITTED work. An uncommitted checkout file has no shared
history to protect, and apply backs the live copy up like any other
overwrite -- so it applies, and says what it did.
"""
from __future__ import annotations

import json
import subprocess as sp
from pathlib import Path

import pytest

from dazzle_claude_config.cli import main

from conftest import GIT_ID

V1 = b"# draft one\nalpha\n"
V2 = b"# draft two\nalpha\nbeta\n"

MANIFEST = {
    "manifest_version": 1,
    "territories": {"dotclaude": {"root_var": "CLAUDE_DIR", "repo_dir": "dotclaude"}},
    "entries": [{"repo": "dotclaude/skills", "territory": "dotclaude",
                 "target": "skills", "strategy": "copy"}],
}


def _git(cwd: Path, *args: str) -> str:
    r = sp.run(["git", *GIT_ID, "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout


@pytest.fixture
def world(tmp_path):
    co = tmp_path / "co"
    sp.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    (co / "dotclaude" / "skills").mkdir(parents=True)
    (co / "ccs-manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (co / "dotclaude" / "skills" / "committed.md").write_bytes(b"# committed\nv1\n")
    _git(co, "add", "-A"); _git(co, "commit", "-qm", "seed v1")
    # Two commits, not one: with a single commit in history the base finder
    # refuses on purpose (HEAD as the sole candidate cannot be told apart
    # from adoption), and every attribution below would read as two-sided.
    (co / "dotclaude" / "skills" / "committed.md").write_bytes(b"# committed\n")
    _git(co, "commit", "-qam", "seed v2")
    live = tmp_path / "live"; live.mkdir()
    (live / "skills").mkdir()
    (live / "skills" / "committed.md").write_bytes(b"# committed\n")
    user = tmp_path / "user"; user.mkdir()
    return dict(co=co, live=live, user=user)


def _ccs(w, *verb) -> list[str]:
    return ["--checkout-dir", str(w["co"]), "--claude-dir", str(w["live"]),
            "--user-claude", str(w["user"]), "--no-color", "--no-fetch", *verb]


def _new_untracked(w, name="draft/SKILL.md", content=V1) -> Path:
    p = w["co"] / "dotclaude" / "skills" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


# -- the authoring loop -------------------------------------------------------

def test_the_author_loop_applies_twice(world, capsys):
    """create untracked -> apply -> edit -> apply. The second one must land."""
    src = _new_untracked(world)
    assert main(_ccs(world, "apply")) == 0
    live = world["live"] / "skills" / "draft" / "SKILL.md"
    assert live.read_bytes() == V1
    capsys.readouterr()

    src.write_bytes(V2)                      # the author's next edit
    rc = main(_ccs(world, "apply"))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert live.read_bytes() == V2, out      # the edit reached the live tree
    assert "local snapshot" not in out
    assert "older than live" not in out


def test_the_overwritten_live_copy_is_backed_up(world, capsys):
    # Applying over live content is still an overwrite, so A3 holds: the
    # previous bytes stay recoverable.
    src = _new_untracked(world)
    main(_ccs(world, "apply"))
    src.write_bytes(V2)
    main(_ccs(world, "apply"))
    saved = list((world["user"] / "backups" / "ccs").rglob("SKILL.md"))
    assert saved and any(p.read_bytes() == V1 for p in saved)


def test_scoped_apply_reaches_it_too(world, capsys):
    # The issue's exact invocation: --only over the entry.
    src = _new_untracked(world)
    main(_ccs(world, "apply", "--only", "dotclaude/skills"))
    capsys.readouterr()
    src.write_bytes(V2)
    rc = main(_ccs(world, "apply", "--only", "dotclaude/skills"))
    out = capsys.readouterr().out
    assert rc == 0
    assert (world["live"] / "skills" / "draft" / "SKILL.md").read_bytes() == V2, out


# -- what status says about it ------------------------------------------------

def test_status_does_not_assert_a_direction_it_cannot_know(world, capsys):
    _new_untracked(world)
    main(_ccs(world, "apply"))
    (world["co"] / "dotclaude" / "skills" / "draft" / "SKILL.md").write_bytes(V2)
    capsys.readouterr()
    main(_ccs(world, "status", "--long"))
    out = capsys.readouterr().out
    assert "history cannot say which side is newer" in out
    assert "older than live" not in out


# -- the summary that lied ----------------------------------------------------

def test_summary_does_not_claim_a_match_when_something_was_held_back(world, capsys):
    # A retired file whose live copy YOU edited is held back, the run copies
    # nothing, and the old summary then claimed live matched the checkout.
    #
    # This used an UNMODIFIED retired copy until #31. Under the `untouched`
    # default that file is now correctly staged away rather than held back,
    # so the old setup no longer produced the state under test. Editing live's
    # copy restores it -- and exercises the more important half of the policy,
    # since a retired file you edited is the one ccs must never remove quietly.
    _git(world["co"], "rm", "-q", "--cached", "dotclaude/skills/committed.md")
    (world["co"] / "dotclaude" / "skills" / "committed.md").unlink()
    _git(world["co"], "commit", "-qm", "remove it")
    (world["live"] / "skills" / "committed.md").write_bytes(
        b"# committed\nand then I edited it locally\n")
    rc = main(_ccs(world, "apply"))
    out = capsys.readouterr().out
    assert "nothing to do" in out
    assert "already matches the checkout" not in out
    assert (world["live"] / "skills" / "committed.md").exists(), \
        "a retired file you edited must not be staged away"


def test_summary_still_claims_a_match_when_truly_clean(world, capsys):
    rc = main(_ccs(world, "apply"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "already matches the checkout" in out


def test_a_direction_skip_alone_stops_the_match_claim(world, capsys):
    # M2: a file skipped for direction is held back too -- not only pending
    # removals. Live-ahead file, nothing else to do: the summary must not
    # claim a match.
    (world["live"] / "skills" / "committed.md").write_bytes(b"# committed\nmine\n")
    rc = main(_ccs(world, "apply"))
    out = capsys.readouterr().out
    assert "live is ahead" in out                      # the skip happened
    assert "already matches the checkout" not in out
    assert "see the skipped and pending lines above" in out


def test_a_committed_empty_file_is_not_called_uncommitted(world, capsys):
    # M6's finding: `git show HEAD:<path>` succeeds with EMPTY output for a
    # committed empty file. Treating empty output as "never committed"
    # mis-attributes a tracked file, so only the return code may decide.
    (world["co"] / "dotclaude" / "skills" / "empty.md").write_bytes(b"")
    _git(world["co"], "add", "-A"); _git(world["co"], "commit", "-qm", "an empty one")
    (world["live"] / "skills" / "empty.md").write_bytes(b"# live added content\n")
    main(_ccs(world, "status", "--long"))
    out = capsys.readouterr().out
    assert "empty.md" in out
    assert "not committed in the checkout yet" not in out


# -- the guard still guards ---------------------------------------------------

def test_a_committed_file_touched_on_both_sides_is_still_refused(world, capsys):
    # The protection this loosening must NOT weaken: a file with shared
    # history, changed on both sides, still refuses a one-way apply.
    (world["co"] / "dotclaude" / "skills" / "committed.md").write_bytes(b"# committed\ntheirs\n")
    _git(world["co"], "commit", "-qam", "their edit")
    (world["live"] / "skills" / "committed.md").write_bytes(b"# committed\nmine\n")
    rc = main(_ccs(world, "apply"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "REFUSING" in out and "BOTH sides" in out


def test_a_failed_write_stops_the_match_claim(world, capsys, monkeypatch):
    """A file that could NOT be written is the most literal held-back work.

    Found by the v0.5.9 release-gate checklist run, not by the suite: the
    `held` check listed direction-skips, pending removals and mismatches but
    omitted r.failed, so a run whose only outcome was a failed write printed
    "your live config already matches the checkout" directly beneath the
    FAILED line naming the file it could not write. The exit code was right
    the whole time; only the sentence lied.
    """
    import shutil as _sh
    from dazzle_claude_config import apply as _apply

    # Live is missing the file, so apply would SEED it -- the one path whose
    # only result can be a failure, which is what makes the summary reachable.
    (world["live"] / "skills" / "committed.md").unlink()

    real_copy = _sh.copy2

    def refuse(src, dst, *a, **k):
        if "committed.md" in str(dst):
            raise OSError(13, "Permission denied")
        return real_copy(src, dst, *a, **k)

    monkeypatch.setattr(_apply.shutil, "copy2", refuse)

    rc = main(_ccs(world, "apply"))
    out = capsys.readouterr().out
    assert "FAILED" in out or "failed" in out          # the truth is printed
    assert "already matches the checkout" not in out, out   # ...and not contradicted
    assert rc != 0
