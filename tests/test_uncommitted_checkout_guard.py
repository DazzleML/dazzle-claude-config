"""collect must not overwrite an uncommitted checkout edit (#13).

The checkout is an editing surface, not only a mirror: settings are edited
there by design, and a payload restructure is hours of working-tree edits.
A file whose working-tree copy differs from HEAD holds work that exists in
no commit and on no other machine, so overwriting it with live's content
destroys the only copy -- silently, exit 0, as the two-machine simulator
measured (scenario L / SC-03).

No base logic can protect it: it was never committed, so it is nobody's
history. Only the verb can.
"""
from __future__ import annotations

import json
import subprocess as sp
from pathlib import Path

import pytest

from dazzle_claude_config.cli import main

from conftest import GIT_ID

COMMITTED = b"# shared\nv1\n"
LIVE_EDIT = b"# shared\nv1\nlive added this\n"
CHECKOUT_EDIT = b"# shared\nv1\nMY UNCOMMITTED WORK\n"

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
    for name in ("shared.md", "other.md"):
        (co / "dotclaude" / "skills" / name).write_bytes(b"# shared\nv0\n")
    _git(co, "add", "-A"); _git(co, "commit", "-qm", "seed v0")
    # A second commit, so the base finder has more than HEAD to choose from
    # (with one commit it refuses on purpose and every file reads two-sided).
    for name in ("shared.md", "other.md"):
        (co / "dotclaude" / "skills" / name).write_bytes(COMMITTED)
    _git(co, "commit", "-qam", "seed v1")
    live = tmp_path / "live"; live.mkdir()
    (live / "skills").mkdir()
    for name in ("shared.md", "other.md"):
        (live / "skills" / name).write_bytes(COMMITTED)
    user = tmp_path / "user"; user.mkdir()
    return dict(co=co, live=live, user=user)


def _ccs(w, *verb) -> list[str]:
    return ["--checkout-dir", str(w["co"]), "--claude-dir", str(w["live"]),
            "--user-claude", str(w["user"]), "--no-color", "--no-fetch", *verb]


def _co_file(w, name="shared.md") -> Path:
    return w["co"] / "dotclaude" / "skills" / name


def _live_file(w, name="shared.md") -> Path:
    return w["live"] / "skills" / name


# -- the scenario the simulator measured --------------------------------------

def test_collect_refuses_to_overwrite_an_uncommitted_checkout_edit(world, capsys):
    _co_file(world).write_bytes(CHECKOUT_EDIT)       # hours of unsaved work
    _live_file(world).write_bytes(LIVE_EDIT)         # live moved too
    rc = main(_ccs(world, "collect"))
    out = capsys.readouterr().out
    assert _co_file(world).read_bytes() == CHECKOUT_EDIT, out   # NOT overwritten
    assert "REFUSING" in out and "shared.md" in out
    assert "uncommitted edit" in out
    assert rc == 1                                   # held back -> not clean


def test_the_refusal_names_all_three_remedies(world, capsys):
    _co_file(world).write_bytes(CHECKOUT_EDIT)
    _live_file(world).write_bytes(LIVE_EDIT)
    main(_ccs(world, "collect"))
    out = capsys.readouterr().out
    assert "commit it" in out
    assert "checkout --" in out                      # discard
    assert "--force" in out                          # overwrite anyway


def test_other_files_still_collect(world, capsys):
    # One dirty file must not stop the rest of the run.
    _co_file(world).write_bytes(CHECKOUT_EDIT)
    _live_file(world).write_bytes(LIVE_EDIT)
    _live_file(world, "other.md").write_bytes(b"# shared\nv1\nother live edit\n")
    main(_ccs(world, "collect"))
    assert _co_file(world, "other.md").read_bytes().endswith(b"other live edit\n")
    assert _co_file(world).read_bytes() == CHECKOUT_EDIT


def test_an_untracked_checkout_file_is_protected_too(world, capsys):
    # `git diff HEAD` would not report this one; porcelain does. Overwriting
    # an untracked checkout file loses work exactly as a modified one does.
    new = _co_file(world, "draft.md")
    new.write_bytes(b"# a draft nobody committed\n")
    _live_file(world, "draft.md").write_bytes(b"# live's different draft\n")
    main(_ccs(world, "collect"))
    out = capsys.readouterr().out
    assert new.read_bytes() == b"# a draft nobody committed\n", out
    assert "draft.md" in out and "REFUSING" in out


def test_force_overwrites_deliberately(world, capsys):
    _co_file(world).write_bytes(CHECKOUT_EDIT)
    _live_file(world).write_bytes(LIVE_EDIT)
    rc = main(_ccs(world, "collect", "--force"))
    assert _co_file(world).read_bytes() == LIVE_EDIT   # the user asked for it
    assert rc == 0


def test_a_clean_checkout_is_unaffected(world, capsys):
    _live_file(world).write_bytes(LIVE_EDIT)
    rc = main(_ccs(world, "collect"))
    out = capsys.readouterr().out
    assert _co_file(world).read_bytes() == LIVE_EDIT
    assert "REFUSING" not in out
    assert rc == 0


def test_summary_does_not_claim_completeness_when_a_file_was_refused(world, capsys):
    # Live equals HEAD everywhere, so nothing WOULD be collected anyway --
    # except the dirty file, which is held back. The old summary would have
    # said the checkout already has everything.
    _co_file(world).write_bytes(CHECKOUT_EDIT)
    main(_ccs(world, "collect"))
    out = capsys.readouterr().out
    if "nothing to do" in out:
        assert "already has everything" not in out


# -- the primitive ------------------------------------------------------------

def test_dirty_paths_reports_modified_staged_and_untracked(world):
    from dazzle_claude_config.gitops import CheckoutRepo
    _co_file(world).write_bytes(CHECKOUT_EDIT)                  # modified
    (world["co"] / "dotclaude" / "skills" / "new.md").write_bytes(b"x\n")  # untracked
    _git(world["co"], "add", "dotclaude/skills/new.md")         # now staged
    (world["co"] / "dotclaude" / "skills" / "late.md").write_bytes(b"y\n")  # untracked
    dirty = CheckoutRepo(world["co"]).dirty_paths()
    assert "dotclaude/skills/shared.md" in dirty
    assert "dotclaude/skills/new.md" in dirty
    assert "dotclaude/skills/late.md" in dirty
    assert "dotclaude/skills/other.md" not in dirty
