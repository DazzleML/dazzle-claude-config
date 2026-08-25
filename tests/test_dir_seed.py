"""Directory `seed-if-absent` entries (issue #28).

Before this feature, a seed entry whose repo path was a directory parsed
cleanly, counted as matched, and silently applied to nothing -- a
narrowing the default-closed manifest forbids. Now it seeds every ABSENT
file under it, recursively; a live file that exists is never touched (the
per-file never-overwrite rule IS the seed contract).
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
    "territories": {"userclaude": {"root_var": "USER_CLAUDE", "repo_dir": "userclaude"}},
    "entries": [{"repo": "userclaude/claude-config/skills", "territory": "userclaude",
                 "target": "claude-config/skills", "strategy": "seed-if-absent"}],
    "deny": ["**/secret.env"],
}


def _git(cwd: Path, *args: str) -> str:
    r = sp.run(["git", *GIT_ID, "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout


@pytest.fixture
def world(tmp_path):
    co = tmp_path / "co"
    sp.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    d = co / "userclaude" / "claude-config" / "skills"
    (d / "alpha").mkdir(parents=True)
    (d / "beta").mkdir()
    (d / "alpha" / "config.env").write_bytes(b"a=1\n")
    (d / "alpha" / "notes.md").write_bytes(b"alpha notes\n")
    (d / "beta" / "config.env").write_bytes(b"b=1\n")
    (d / "secret.env").write_bytes(b"nope\n")
    (co / "ccs-manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (co / "dotclaude").mkdir()
    _git(co, "add", "-A"); _git(co, "commit", "-qm", "seed dir")
    live = tmp_path / "live"; live.mkdir()
    user = tmp_path / "user"; user.mkdir()
    return dict(co=co, live=live, user=user)


def _apply(w, *extra) -> int:
    return main(["--checkout-dir", str(w["co"]), "--claude-dir", str(w["live"]),
                 "--user-claude", str(w["user"]), "--no-color", "--no-fetch",
                 "apply", *extra])


def test_directory_seed_seeds_every_absent_file(world, capsys):
    rc = _apply(world)
    out = capsys.readouterr().out
    root = world["user"] / "claude-config" / "skills"
    assert (root / "alpha" / "config.env").read_bytes() == b"a=1\n"
    assert (root / "alpha" / "notes.md").is_file()
    assert (root / "beta" / "config.env").is_file()
    assert out.count("seeded") >= 3


def test_directory_seed_never_overwrites_an_existing_file(world, capsys):
    mine = world["user"] / "claude-config" / "skills" / "alpha" / "config.env"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"a=MINE\n")
    _apply(world)
    assert mine.read_bytes() == b"a=MINE\n"          # untouched
    assert (mine.parent / "notes.md").is_file()       # siblings still seeded


def test_directory_seed_honours_the_deny_list_per_file(world, capsys):
    _apply(world)
    out = capsys.readouterr().out
    assert not (world["user"] / "claude-config" / "skills" / "secret.env").exists()
    assert "secret.env" in out                        # refused, loudly


def test_only_scopes_inside_a_directory_seed(world, capsys):
    rc = _apply(world, "--only", "userclaude/claude-config/skills/beta")
    root = world["user"] / "claude-config" / "skills"
    assert (root / "beta" / "config.env").is_file()
    assert not (root / "alpha").exists()              # out of scope, untouched


def test_reseed_names_one_file_inside_the_directory(world, capsys):
    _apply(world)
    target = world["user"] / "claude-config" / "skills" / "alpha" / "config.env"
    target.write_bytes(b"a=OLD\n")
    rc = _apply(world, "--reseed", "claude-config/skills/alpha/config.env")
    out = capsys.readouterr().out
    assert target.read_bytes() == b"a=1\n"            # fresh seed taken
    assert "backup" in out.lower()                    # old copy saved first


def test_reseed_accepts_the_windows_backslash_form(world, capsys):
    # Mutation-sweep closure (M10): a --reseed path typed with backslashes
    # (the form a Windows user actually types) must match inside a
    # directory seed.
    _apply(world)
    target = world["user"] / "claude-config" / "skills" / "alpha" / "config.env"
    target.write_bytes(b"a=OLD\n")
    _apply(world, "--reseed", "claude-config\\skills\\alpha\\config.env")
    assert target.read_bytes() == b"a=1\n"


def test_dry_run_reports_without_writing(world, capsys):
    rc = _apply(world, "--dry-run")
    assert not (world["user"] / "claude-config").exists()
