"""Retired files: remove what is not yours, keep what is (#31).

When the payload deliberately deletes a file, every box keeps its live copy
until a human runs `ccs apply --sync-removals`. That is a manual step per box
per retirement, and until it happens the box is half-migrated -- carrying both
`commands/addendum.md` and `skills/addendum/SKILL.md`, so both load, which is
the exact ambiguity the migration existed to remove.

The default is now `untouched`, and the NARROWING matters more than the
default: a retired file is staged away only when the live copy still matches a
committed version, i.e. holds nothing of the user's. A copy the user edited is
reported instead. Staging that away silently is the one thing this tool
refuses to do.
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

RETIRED = b"# retired\nthe payload deleted this\n"
KEPT = b"# kept\nstill here\n"


def _git(cwd: Path, *args: str) -> None:
    r = sp.run(["git", *GIT_ID, "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"


@pytest.fixture
def world(tmp_path):
    co, live, user = tmp_path / "co", tmp_path / "live", tmp_path / "user"
    (co / "dotclaude" / "skills").mkdir(parents=True)
    (live / "skills").mkdir(parents=True)
    user.mkdir()
    (co / "ccs-manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    sp.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    (co / "dotclaude" / "skills" / "kept.md").write_bytes(KEPT)
    (co / "dotclaude" / "skills" / "retired.md").write_bytes(RETIRED)
    _git(co, "add", "-A"); _git(co, "commit", "-qm", "v1")
    # live holds both, unmodified
    (live / "skills" / "kept.md").write_bytes(KEPT)
    (live / "skills" / "retired.md").write_bytes(RETIRED)
    # the payload RETIRES one of them
    (co / "dotclaude" / "skills" / "retired.md").unlink()
    _git(co, "rm", "-q", "--cached", "dotclaude/skills/retired.md")
    _git(co, "commit", "-qm", "v2: retire retired.md")
    return dict(co=co, live=live, user=user)


def _ccs(w, *verb) -> list[str]:
    return ["--checkout-dir", str(w["co"]), "--claude-dir", str(w["live"]),
            "--user-claude", str(w["user"]), "--no-color", "--no-fetch", *verb]


def _cfg(w, **keys) -> None:
    (w["user"] / "ccs-config.json").write_text(json.dumps(keys), encoding="utf-8")


def _retired(w) -> Path:
    return w["live"] / "skills" / "retired.md"


# -- the default, and its narrowing -------------------------------------------

def test_an_unmodified_retired_file_is_removed_by_default(world, capsys):
    main(_ccs(world, "apply"))
    out = capsys.readouterr().out
    assert not _retired(world).exists(), out
    assert "removed" in out and "retired upstream" in out
    assert "unmodified" in out                    # names the REASON, not the mechanism


def test_a_retired_file_YOU_edited_is_kept(world, capsys):
    _retired(world).write_bytes(RETIRED + b"and I added this line\n")
    main(_ccs(world, "apply"))
    out = capsys.readouterr().out
    assert _retired(world).exists(), out
    assert "kept" in out
    assert "YOUR copy differs" in out
    assert "ccs collect" in out and "--sync-removals" in out   # both ways out


def test_the_kept_file_keeps_your_content_exactly(world, capsys):
    mine = RETIRED + b"and I added this line\n"
    _retired(world).write_bytes(mine)
    main(_ccs(world, "apply"))
    assert _retired(world).read_bytes() == mine


def test_a_removal_is_staged_to_backup_not_destroyed(world, capsys):
    main(_ccs(world, "apply"))
    backups = world["user"] / "backups" / "ccs"
    staged = list(backups.rglob("retired.md"))
    assert staged, "a removal must be recoverable from the backup dir"
    assert staged[0].read_bytes() == RETIRED


def test_a_file_never_in_the_checkout_is_left_alone(world, capsys):
    """Local additions are not retirements and must never be staged away."""
    (world["live"] / "skills" / "mine.md").write_bytes(b"# mine\nnever in the payload\n")
    main(_ccs(world, "apply"))
    out = capsys.readouterr().out
    assert (world["live"] / "skills" / "mine.md").exists(), out
    assert "local only" in out


# -- the other two policies ---------------------------------------------------

def test_never_reports_and_stages_nothing(world, capsys):
    _cfg(world, sync_removals="never")
    main(_ccs(world, "apply"))
    out = capsys.readouterr().out
    assert _retired(world).exists(), out
    assert "PENDING" in out


def test_all_stages_even_a_file_you_edited(world, capsys):
    _retired(world).write_bytes(RETIRED + b"my edit\n")
    _cfg(world, sync_removals="all")
    main(_ccs(world, "apply"))
    assert not _retired(world).exists()


def test_the_flag_beats_the_config_in_both_directions(world, capsys):
    """A setting you cannot override for one run is a trap."""
    _retired(world).write_bytes(RETIRED + b"my edit\n")
    _cfg(world, sync_removals="never")
    main(_ccs(world, "apply", "--sync-removals"))       # never -> all
    assert not _retired(world).exists()


def test_no_sync_removals_beats_an_aggressive_config(world, capsys):
    _cfg(world, sync_removals="all")
    main(_ccs(world, "apply", "--no-sync-removals"))    # all -> never
    assert _retired(world).exists()


def test_the_env_var_is_honoured(world, capsys, monkeypatch):
    monkeypatch.setenv("CCS_SYNC_REMOVALS", "never")
    main(_ccs(world, "apply"))
    assert _retired(world).exists()


# -- safety ------------------------------------------------------------------

def test_an_unrecognised_policy_falls_back_to_the_SAFEST_value(world, capsys):
    """A typo must never widen what the tool deletes."""
    _cfg(world, sync_removals="untouced")              # note the typo
    main(_ccs(world, "apply"))
    out = capsys.readouterr().out
    assert _retired(world).exists(), out               # nothing staged
    assert "not one of" in out and "never" in out


def test_a_detached_checkout_does_not_auto_remove(world, capsys):
    """`ccs git checkout <old-sha>` then apply would make everything added
    since look retired. Backed up, but alarming and easy to trigger."""
    head = sp.run(["git", "-C", str(world["co"]), "rev-parse", "HEAD"],
                  capture_output=True, text=True).stdout.strip()
    _git(world["co"], "checkout", "-q", head)          # detached
    main(_ccs(world, "apply"))
    out = capsys.readouterr().out
    assert _retired(world).exists(), out
    assert "not staging retired files" in out
    assert "not on a branch" in out


def test_an_explicit_flag_still_works_on_a_detached_checkout(world, capsys):
    """The guard stands down the AUTOMATIC policy, not the user's explicit ask."""
    head = sp.run(["git", "-C", str(world["co"]), "rev-parse", "HEAD"],
                  capture_output=True, text=True).stdout.strip()
    _git(world["co"], "checkout", "-q", head)
    main(_ccs(world, "apply", "--sync-removals"))
    assert not _retired(world).exists()


def test_a_deny_listed_file_can_never_be_staged_away(world, capsys):
    """Denied files never enter live_only at all -- verify that structurally."""
    manifest = dict(MANIFEST, deny=["**/secret.md"])
    (world["co"] / "ccs-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (world["live"] / "skills" / "secret.md").write_bytes(b"local secret\n")
    main(_ccs(world, "apply"))
    assert (world["live"] / "skills" / "secret.md").exists()


def test_a_checkout_BEHIND_its_upstream_does_not_auto_remove(world, capsys, tmp_path):
    """The other half of the stale guard, and the one that fires in practice.

    A box that has not pulled is looking at an older tree. Files another
    machine added since are present in history and absent from this worktree,
    which is indistinguishable from a retirement -- so an automatic policy
    would stage them away on a box that is merely out of date.

    Caught by mutation K6: the detached-HEAD half had a test and this half
    did not, so removing it changed nothing that anything checked.
    """
    # Give the checkout an upstream that is one commit ahead of it.
    bare = tmp_path / "remote.git"
    sp.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(world["co"], "remote", "add", "origin", str(bare))
    _git(world["co"], "push", "-q", "-u", "origin", "main")
    clone = tmp_path / "clone"
    sp.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    (clone / "dotclaude" / "skills" / "from-elsewhere.md").write_bytes(b"# added by another box\n")
    _git(clone, "add", "-A"); _git(clone, "commit", "-qm", "another box adds a file")
    _git(clone, "push", "-q")
    _git(world["co"], "fetch", "-q", "origin")     # now behind, without pulling

    rc = main(_ccs(world, "apply"))
    out = capsys.readouterr().out
    assert _retired(world).exists(), out
    assert "not staging retired files" in out, out
    assert "behind its upstream" in out, out
