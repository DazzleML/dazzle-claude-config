"""`ccs setup box` and `ccs doctor` (issue #26, slices 1-2).

Both verbs exist for environments that are NOT configured yet -- the state
every other verb refuses -- so these tests exercise broken and partial
worlds on purpose: the missing box file that a real flip reached apply
with, the absent checkout, the plain directory that is not a repo.
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
    "entries": [{"repo": "dotclaude/CLAUDE.md", "territory": "dotclaude",
                 "target": "CLAUDE.md", "strategy": "seed-if-absent"}],
}


def _git(cwd: Path, *args: str) -> str:
    r = sp.run(["git", *GIT_ID, "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout


@pytest.fixture
def world(tmp_path):
    co = tmp_path / "co"
    sp.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    (co / "dotclaude").mkdir()
    (co / "ccs-manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (co / "dotclaude" / "CLAUDE.md").write_bytes(b"seed\n")
    _git(co, "add", "-A"); _git(co, "commit", "-qm", "seed")
    live = tmp_path / "live"; live.mkdir()
    user = tmp_path / "user"; user.mkdir()
    return dict(co=co, live=live, user=user)


def _args(w, *verb) -> list[str]:
    return ["--checkout-dir", str(w["co"]), "--claude-dir", str(w["live"]),
            "--user-claude", str(w["user"]), "--no-color", "--no-fetch", *verb]


# -- setup box ----------------------------------------------------------------

def test_setup_box_declares_name_and_tags(world, capsys):
    rc = main(_args(world, "setup", "box", "--name", "testbox",
                    "--tag", "production"))
    out = capsys.readouterr().out
    assert rc == 0 and "declared: box testbox" in out
    data = json.loads((world["user"] / "ccs-box.json").read_text())
    # the name is always a tag too, first
    assert data == {"name": "testbox", "tags": ["testbox", "production"]}


def test_setup_box_never_overwrites(world, capsys):
    main(_args(world, "setup", "box", "--name", "one"))
    rc = main(_args(world, "setup", "box", "--name", "two"))
    out = capsys.readouterr().out
    assert rc == 0 and "never overwrites" in out
    assert json.loads((world["user"] / "ccs-box.json").read_text())["name"] == "one"


def test_setup_box_rejects_a_bad_name(world, capsys):
    rc = main(_args(world, "setup", "box", "--name", "Bad Name"))
    assert rc == 2
    assert "lowercase" in capsys.readouterr().err


def test_setup_box_needs_a_name_when_not_interactive(world, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    rc = main(_args(world, "setup", "box"))
    assert rc == 2
    assert "--name" in capsys.readouterr().err


def test_bare_setup_runs_the_doctor(world, capsys):
    # Bare `ccs setup` must land somewhere useful (the doctor overview),
    # never an argparse usage error (user finding, 2026-08-25).
    rc = main(_args(world, "setup"))
    out = capsys.readouterr().out
    assert rc == 1                              # missing box file -> WARN
    assert "no box file" in out and "ccs setup box" in out
    assert "doctor:" in out


def test_setup_box_works_without_a_checkout(tmp_path, capsys):
    # The whole point: configuring a machine BEFORE everything else exists.
    user = tmp_path / "u"; user.mkdir()
    rc = main(["--checkout-dir", str(tmp_path / "nowhere"), "--user-claude",
               str(user), "--no-color", "setup", "box", "--name", "fresh"])
    assert rc == 0
    assert (user / "ccs-box.json").is_file()


def test_write_box_api_validates_the_name_itself(world):
    # Mutation-sweep closure (M12): the CLI path is shielded because the
    # name is always also a tag, but the PUBLIC API must validate the name
    # even when every supplied tag is valid -- defense in depth.
    from dazzle_claude_config import boxconfig
    path, errs = boxconfig.write_box("Bad Name", ["oktag"], world["user"])
    assert path is None
    assert any("lowercase" in e for e in errs)
    assert not (world["user"] / "ccs-box.json").exists()


# -- doctor -------------------------------------------------------------------

def _doctor(w, capsys, **kw):
    rc = main(_args(w, "doctor"))
    return rc, capsys.readouterr().out


def test_doctor_all_ok_on_a_configured_world(world, tmp_path, capsys):
    main(_args(world, "setup", "box", "--name", "testbox"))
    # A configured world includes a remote -- doctor rightly warns on a
    # payload with nowhere to push (--no-fetch keeps this offline).
    bare = tmp_path / "origin.git"
    sp.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    _git(world["co"], "remote", "add", "origin", str(bare))
    capsys.readouterr()
    rc, out = _doctor(world, capsys)
    assert rc == 0, out
    assert "fully configured" in out


def test_doctor_warns_on_a_remoteless_checkout(world, capsys):
    main(_args(world, "setup", "box", "--name", "testbox"))
    capsys.readouterr()
    rc, out = _doctor(world, capsys)
    assert rc == 1
    assert "no remote named origin" in out


def test_doctor_warns_on_the_missing_box_file(world, capsys):
    rc, out = _doctor(world, capsys)
    assert rc == 1
    assert "no box file" in out and "ccs setup box" in out


def test_doctor_fails_on_a_missing_checkout(tmp_path, capsys):
    user = tmp_path / "u"; user.mkdir()
    rc = main(["--checkout-dir", str(tmp_path / "nope"), "--user-claude",
               str(user), "--no-color", "--no-fetch", "doctor"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "checkout not found" in out
    assert "not usable" in out


def test_doctor_warns_on_malformed_user_config(world, capsys):
    main(_args(world, "setup", "box", "--name", "testbox"))
    (world["user"] / "ccs-config.json").write_text("{oops", encoding="utf-8")
    capsys.readouterr()
    rc, out = _doctor(world, capsys)
    assert rc == 1
    assert "not valid JSON" in out


def test_doctor_surfaces_seed_questions(world, capsys):
    # The #27 integration: doctor repeats status's actionable seed findings.
    main(_args(world, "setup", "box", "--name", "testbox"))
    (world["live"] / "CLAUDE.md").write_bytes(b"customized\n")
    capsys.readouterr()
    rc, out = _doctor(world, capsys)
    assert rc == 1
    assert "seeded CLAUDE.md" in out and "Yours or the payload's?" in out


def test_doctor_reads_only(world, capsys):
    # Doctor on a broken world changes nothing: no box file appears.
    _doctor(world, capsys)
    assert not (world["user"] / "ccs-box.json").exists()
