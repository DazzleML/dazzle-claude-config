"""`ccs merge|collect|apply <path>` -- one file, named the way `ccs diff` takes it.

Found 2026-09-02: `ccs merge skills/think/SKILL.md` errored with *unrecognized
arguments* because only `--only` existed, while `ccs diff think/SKILL.md`
worked. The two resolve differently -- `--only` matches the repo-side label
component-wise, `diff` resolves a whole-component suffix and lists ambiguity
-- so the positional must resolve as `diff` does and hand the verb the
qualified repo label as its `--only` scope. A bare alias would have kept the
inconsistency.
"""
from __future__ import annotations

import subprocess as sp
from types import SimpleNamespace

import pytest

from dazzle_claude_config import boxconfig
from dazzle_claude_config.cli import _fold_path_into_only, main
from dazzle_claude_config.manifest import Manifest

from conftest import GIT_ID


def _git(co, *args: str) -> str:
    r = sp.run(["git", *GIT_ID, "-C", str(co), *args], capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout


@pytest.fixture
def world(tmp_path):
    """Two directory entries, `s/` and `t/`, each holding SAME.md, plus a
    single-file entry -- so a bare `SAME.md` is ambiguous, `t/SAME.md` is
    not, and `think/SKILL.md` reaches inside `skills`. Live edits every file
    so the differing set contains them."""
    co = tmp_path / "checkout"
    for d in ("s", "t", "skills/think"):
        (co / "dotclaude" / d).mkdir(parents=True)
    live = tmp_path / "live"
    for d in ("s", "t", "skills/think"):
        (live / d).mkdir(parents=True)
    user = tmp_path / "user"
    user.mkdir()
    sp.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    (co / "ccs-manifest.json").write_text(
        '{"manifest_version":1,'
        '"territories":{"dotclaude":{"root_var":"CLAUDE_DIR","repo_dir":"dotclaude"}},'
        '"entries":['
        '{"repo":"dotclaude/s","territory":"dotclaude","target":"s","strategy":"copy"},'
        '{"repo":"dotclaude/t","territory":"dotclaude","target":"t","strategy":"copy"},'
        '{"repo":"dotclaude/skills","territory":"dotclaude","target":"skills","strategy":"copy"},'
        '{"repo":"dotclaude/SEED.md","territory":"dotclaude","target":"SEED.md",'
        '"strategy":"seed-if-absent"}'
        ']}', encoding="utf-8")
    for rel in ("s/SAME.md", "t/SAME.md", "skills/think/SKILL.md", "SEED.md"):
        (co / "dotclaude" / rel).write_text(f"{rel} checkout\n", encoding="utf-8")
    _git(co, "add", "-A"); _git(co, "commit", "-qm", "seed")
    for rel in ("s/SAME.md", "t/SAME.md", "skills/think/SKILL.md", "SEED.md"):
        (live / rel).write_text(f"{rel} live edit\n", encoding="utf-8")
    return co, live, user


def _argv(co, live, user, *rest):
    return ["--checkout-dir", str(co), "--claude-dir", str(live),
            "--user-claude", str(user), "--no-color", "--no-fetch", *rest]


def _fold(co, live, user, verb, path, only=None):
    """Drive the resolver the way main() does, and return the scope it set."""
    manifest = Manifest.load(co)
    roots = {"CLAUDE_DIR": live, "USER_CLAUDE": user}
    args = SimpleNamespace(verb=verb, path=path, only=only)
    rc = _fold_path_into_only(args, manifest, co, roots, boxconfig.load(user))
    return rc, args.only


# -- resolution --------------------------------------------------------------

@pytest.mark.parametrize("verb", ["merge", "collect", "apply"])
def test_a_suffix_resolves_to_the_qualified_repo_label(world, verb):
    co, live, user = world
    rc, only = _fold(co, live, user, verb, "think/SKILL.md")
    assert rc is None
    assert only == "dotclaude/skills/think/SKILL.md"


def test_a_qualified_path_disambiguates(world):
    co, live, user = world
    rc, only = _fold(co, live, user, "merge", "t/SAME.md")
    assert rc is None and only == "dotclaude/t/SAME.md"


def test_backslashes_and_a_leading_slash_are_tolerated(world):
    co, live, user = world
    rc, only = _fold(co, live, user, "apply", "\\think\\SKILL.md")
    assert rc is None and only == "dotclaude/skills/think/SKILL.md"


def test_a_seed_entry_is_reachable_through_the_manifest(world):
    """diff_all walks copy entries only, so the file-level resolver never sees
    a seed-if-absent entry -- yet that is the very file merge's seed refusal
    tells you to name. The fold falls back to the manifest for it."""
    co, live, user = world
    rc, only = _fold(co, live, user, "merge", "SEED.md")
    assert rc is None and only == "dotclaude/SEED.md"


def test_diff_is_untouched_by_the_fold(world):
    """`diff` has its own positional and its own handling; the fold must not
    turn it into a --only scope diff does not read."""
    co, live, user = world
    rc, only = _fold(co, live, user, "diff", "t/SAME.md")
    assert rc is None and only is None


# -- refusals, each with the reason on stderr ---------------------------------

def test_ambiguity_is_listed_never_guessed(world, capsys):
    co, live, user = world
    rc = main(_argv(co, live, user, "merge", "--dry-run", "--no-launch", "SAME.md"))
    _out, err = capsys.readouterr()
    assert rc == 2, err
    assert "ambiguous" in err and "2 files" in err
    assert "dotclaude/s/SAME.md" in err and "dotclaude/t/SAME.md" in err


def test_unknown_file_is_an_error_not_a_silent_empty_run(world, capsys):
    co, live, user = world
    rc = main(_argv(co, live, user, "apply", "--dry-run", "nope/NOPE.md"))
    _out, err = capsys.readouterr()
    assert rc == 2 and "no such file" in err


def test_both_forms_at_once_is_refused(world, capsys):
    co, live, user = world
    rc = main(_argv(co, live, user, "collect", "--dry-run",
                    "--only", "dotclaude/t", "t/SAME.md"))
    _out, err = capsys.readouterr()
    assert rc == 2 and "not both" in err


# -- end to end: the scope actually narrows the verb --------------------------

def test_collect_dry_run_is_scoped_to_the_named_file(world, capsys):
    co, live, user = world
    rc = main(_argv(co, live, user, "collect", "--dry-run", "t/SAME.md"))
    out, err = capsys.readouterr()
    assert rc in (0, 1), (out, err)
    assert "t/SAME.md" in out
    assert "s/SAME.md" not in out and "SKILL.md" not in out


def test_merge_dry_run_is_scoped_to_the_named_file(world, capsys):
    co, live, user = world
    rc = main(_argv(co, live, user, "merge", "--dry-run", "--no-launch",
                    "think/SKILL.md"))
    out, err = capsys.readouterr()
    assert rc in (0, 1), (out, err)
    assert "SKILL.md" in out
    assert "SAME.md" not in out
