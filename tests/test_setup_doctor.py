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


# -- doctor's config section (#32, the sixth acceptance criterion) -------------
#
# Doctor used to ask one question about the config file: does it parse? A file
# can parse perfectly and still be missing five settings this version knows --
# which is the entire reason #32 exists. v0.5.10 made it concrete by adding a
# default that MOVES FILES, so a machine could acquire a file-touching
# behaviour on upgrade with nothing in its own config recording that the
# setting existed, and nothing anywhere telling anyone.

def _cfg(w, body: dict):
    (w["user"] / "ccs-config.json").write_text(
        json.dumps(body, indent=2), encoding="utf-8")


#: A config as an older ccs would have written it -- the shape measured on a
#: real second machine, which held 6 of the settings this version knows.
OLD_SHAPE = {"on_divergence": "prompt", "difftool": None, "interactive": True,
             "status_detail": "auto", "status_max_lines": 30, "fetch": True}


def test_doctor_reports_a_config_that_lacks_settings_and_NAMES_them(
        world, capsys):
    """A count is not actionable. The reader needs to know it is
    `sync_removals` in particular, because that is the one that moves files."""
    main(_args(world, "setup", "box", "--name", "testbox"))
    _cfg(world, OLD_SHAPE)
    capsys.readouterr()
    rc, out = _doctor(world, capsys)
    assert rc == 1, out
    assert "sync_removals" in out, out
    assert "auto_pull" in out and "require_current" in out, out
    assert "ccs setup update" in out, "name the command that fixes it"
    assert "without changing anything you set" in out, (
        "the reason people hesitate to run it is fear it will edit their "
        "settings; say that it will not")


def test_doctor_is_quiet_about_a_config_that_is_current(world, capsys):
    from dazzle_claude_config import userconfig
    main(_args(world, "setup", "box", "--name", "testbox"))
    _cfg(world, dict(userconfig.DEFAULTS))
    capsys.readouterr()
    rc, out = _doctor(world, capsys)
    assert "none missing" in out, out
    assert "predates" not in out, out


def test_an_absent_config_is_reported_but_is_NOT_a_warning(world, capsys):
    """Running with no config file is the designed state and behaves safely.
    Flagging every fresh machine yellow for doing nothing wrong is how people
    learn to ignore doctor -- but it is still worth SAYING, because safe and
    visible are different things and #32 is about the second."""
    main(_args(world, "setup", "box", "--name", "testbox"))
    capsys.readouterr()
    rc, out = _doctor(world, capsys)
    # Assert on the LINE, not the exit code: this world has no remote, so
    # doctor rightly warns about that, and checking rc here would be
    # measuring somebody else's finding.
    line = next(ln for ln in out.splitlines() if "no config file yet" in ln)
    assert line.strip().startswith("[ OK ]"), (
        f"an absent config is the designed state, not a warning:\n{line}")
    assert "built-in defaults in effect" in line
    assert "ccs setup update" in line


def test_doctor_reports_an_unknown_key_and_does_not_call_it_broken(
        world, capsys):
    """It usually means a NEWER ccs wrote the file. Worth a look -- it can
    equally be a typo that is silently doing nothing -- but the file is not
    damaged and ccs will not touch the key."""
    from dazzle_claude_config import userconfig
    body = dict(userconfig.DEFAULTS)
    body["from_a_newer_ccs"] = True
    main(_args(world, "setup", "box", "--name", "testbox"))
    _cfg(world, body)
    capsys.readouterr()
    rc, out = _doctor(world, capsys)
    assert "from_a_newer_ccs" in out, out
    assert "left alone" in out, out


def test_doctor_says_NOT_VALID_JSON_rather_than_the_parser_s_word(
        world, capsys):
    main(_args(world, "setup", "box", "--name", "testbox"))
    (world["user"] / "ccs-config.json").write_text("{oops", encoding="utf-8")
    capsys.readouterr()
    rc, out = _doctor(world, capsys)
    assert "not valid JSON" in out, out
    assert "JSONDecodeError" not in out, (
        "'JSONDecodeError' tells the reader nothing they can act on\n" + out)


def test_a_config_that_is_a_LIST_is_not_called_invalid_json(world, capsys):
    """`[1, 2, 3]` IS valid JSON and still cannot be a config. Calling that
    'not valid JSON' sends someone hunting for a syntax error that is not
    there -- the two failures are different and must stay different."""
    main(_args(world, "setup", "box", "--name", "testbox"))
    (world["user"] / "ccs-config.json").write_text("[1, 2, 3]", encoding="utf-8")
    capsys.readouterr()
    rc, out = _doctor(world, capsys)
    assert "not a JSON object" in out, out
    assert "not valid JSON" not in out, out


def test_doctor_reads_the_config_and_writes_NOTHING(world, capsys):
    """doctor is read-only by contract -- it runs on a read-only-policy box.
    Hashing the whole scratch world is the only check that actually proves it,
    because a write to any file in it would be a contract breach."""
    import hashlib

    def fingerprint(root: Path) -> dict:
        return {str(p.relative_to(root)):
                hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(root.rglob("*")) if p.is_file()}

    main(_args(world, "setup", "box", "--name", "testbox"))
    _cfg(world, OLD_SHAPE)
    before = {k: fingerprint(world[k]) for k in ("co", "live", "user")}
    capsys.readouterr()
    _doctor(world, capsys)
    after = {k: fingerprint(world[k]) for k in ("co", "live", "user")}
    assert before == after, "doctor must not write anything, anywhere"
