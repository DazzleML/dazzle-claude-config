"""Remote ergonomics (v0.5.1): `ccs git` passthrough, `auto_pull`, remote leg.

Design: 2026-08-24__23-08-00__dev-workflow-process__v051-remote-ergonomics-ccs-git-auto-pull-remote-leg.md
(personal vault). Issues #25 (passthrough + auto_pull) and #22 (remote leg).

The passthrough splits argv BEFORE argparse (the dazzlecmd dispatch
pattern): everything after the literal token `git` belongs to git,
verbatim. auto_pull is strictly fast-forward-only and status-scoped: a
divergent branch or a dirty file is reported in git's own words, never
merged, rebased, or stashed. The remote leg gives the hub its own labelled
status line instead of a clause under `checkout`.
"""
from __future__ import annotations

import json
import subprocess as sp
from pathlib import Path

import pytest

from dazzle_claude_config import cli
from dazzle_claude_config.cli import (_FLAG_GLOBALS, _VALUE_GLOBALS,
                                      _build_parser, _split_git_passthrough,
                                      main)
from dazzle_claude_config.render import humanize_remote, remote_host

from conftest import GIT_ID

V0 = b"a\nb\n"
V1 = b"a\nb\nc\n"
V2 = b"a\nb\nc\nfrom-the-other-machine\n"

MANIFEST = {
    "manifest_version": 1,
    "territories": {"dotclaude": {"root_var": "CLAUDE_DIR", "repo_dir": "dotclaude"}},
    "entries": [{"repo": "dotclaude/F.md", "territory": "dotclaude",
                 "target": "F.md", "strategy": "copy"}],
}


def _git(cwd: Path, *args: str) -> str:
    r = sp.run(["git", *GIT_ID, "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout


@pytest.fixture
def fleet(tmp_path):
    """Bare remote + tracking checkout + a second clone that can push."""
    bare = tmp_path / "remote.git"
    sp.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    other = tmp_path / "other"
    sp.run(["git", "-c", "core.autocrlf=false", "clone", "-q", str(bare), str(other)],
           check=True, capture_output=True)
    (other / "dotclaude").mkdir()
    (other / "ccs-manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (other / "dotclaude" / "F.md").write_bytes(V0)
    _git(other, "add", "-A"); _git(other, "commit", "-qm", "seed v0")
    (other / "dotclaude" / "F.md").write_bytes(V1)
    _git(other, "commit", "-qam", "seed v1")
    _git(other, "push", "-q", "-u", "origin", "main")
    co = tmp_path / "checkout"
    sp.run(["git", "-c", "core.autocrlf=false", "clone", "-q", str(bare), str(co)],
           check=True, capture_output=True)
    live = tmp_path / "live"; live.mkdir()
    user = tmp_path / "user"; user.mkdir()
    (live / "F.md").write_bytes(V1)
    return dict(bare=bare, other=other, co=co, live=live, user=user)


def _ccs(w, *verb) -> list[str]:
    return ["--checkout-dir", str(w["co"]), "--claude-dir", str(w["live"]),
            "--user-claude", str(w["user"]), "--no-color", *verb]


def _push_from_other(w, content=V2):
    (w["other"] / "dotclaude" / "F.md").write_bytes(content)
    _git(w["other"], "commit", "-qam", "other machine advances")
    _git(w["other"], "push", "-q")


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


# -- the argv split, on its own -----------------------------------------------

def test_split_plain_git_run():
    assert _split_git_passthrough(["git", "pull"]) == ({}, ["pull"])


def test_split_consumes_globals_before_git_only():
    seen, rest = _split_git_passthrough(
        ["--no-color", "--checkout-dir", "X", "git", "log", "--oneline"])
    assert seen == {"--no-color": True, "--checkout-dir": "X"}
    assert rest == ["log", "--oneline"]


def test_split_equals_form():
    seen, rest = _split_git_passthrough(["--checkout-dir=X", "git", "status"])
    assert seen == {"--checkout-dir": "X"} and rest == ["status"]


def test_split_never_touches_anything_after_git():
    # Tokens that LOOK like ccs flags belong to git once the verb is seen.
    seen, rest = _split_git_passthrough(
        ["git", "log", "--no-color", "--checkout-dir", "-h", "--", "x"])
    assert seen == {}
    assert rest == ["log", "--no-color", "--checkout-dir", "-h", "--", "x"]


@pytest.mark.parametrize("argv", [
    [], ["status"], ["-h"], ["--version"], ["diff", "git"],
    ["--checkout-dir"],            # value flag missing its value: argparse's error
    ["--unknown", "git", "pull"],  # unknown token first: argparse's error
])
def test_split_declines_non_git_runs(argv):
    assert _split_git_passthrough(argv) is None


def test_split_globals_cannot_drift_from_the_parser():
    """The hand-rolled scan and _add_common must describe the same flags."""
    value, flag = set(), set()
    for a in _build_parser()._actions:
        for opt in a.option_strings:
            if opt in ("-h", "-V", "--help", "--version"):
                continue
            (value if a.nargs != 0 else flag).add(opt)
    assert value == set(_VALUE_GLOBALS)
    assert flag == set(_FLAG_GLOBALS)


def test_version_shows_the_full_build_string():
    # Two builds of one release must be distinguishable at the prompt
    # (v0.5.3): `-V`/`--version` shows DISPLAY_VERSION plus the hook-stamped
    # build string. Anchor by construction: the old format had no "(".
    from dazzle_claude_config import _version
    for flag in ("-V", "--version"):
        with pytest.raises(SystemExit) as e:
            _build_parser().parse_args([flag])
        assert e.value.code == 0


def test_version_string_carries_the_build_id(capsys):
    from dazzle_claude_config import _version
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["--version"])
    out = capsys.readouterr().out
    assert _version.__version__ in out          # the full build string
    assert "(" in out and out.startswith("ccs ")


# -- the git verb, end to end -------------------------------------------------

def test_ccs_git_runs_in_the_checkout_from_anywhere(fleet, capfd):
    rc = main(_ccs(fleet, "git", "log", "--oneline", "-1"))
    out = capfd.readouterr().out
    assert rc == 0
    assert "seed v1" in out


def test_ccs_git_passes_the_exit_code_through(fleet, capfd):
    rc = main(_ccs(fleet, "git", "rev-parse", "--verify", "-q", "no-such-ref"))
    assert rc != 0


def test_ccs_git_bare_prints_the_resolved_checkout(fleet, capsys):
    rc = main(_ccs(fleet, "git"))
    out = capsys.readouterr().out
    assert rc == 0
    assert str(fleet["co"]) in out
    assert "ccs git pull" in out


def test_ccs_git_refuses_a_nonexistent_checkout_dir(tmp_path, capsys):
    # Tester-found defect (v0.5.1 checklist run-01): a NONEXISTENT
    # --checkout-dir crashed with NotADirectoryError from subprocess before
    # any guard ran. Must be a clean refusal like every other verb's.
    gone = tmp_path / "never-created"
    rc = main(["--checkout-dir", str(gone), "--no-color", "git", "log"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "checkout not found" in err
    assert "Traceback" not in err


def test_ccs_git_refuses_a_non_repo(tmp_path, capsys):
    plain = tmp_path / "plain"; plain.mkdir()
    rc = main(["--checkout-dir", str(plain), "--no-color", "git", "status"])
    err = capsys.readouterr().err
    assert rc == 2
    # Either refusal is correct: "not a git repository" normally, or the A4
    # home-repo guard when %TEMP% itself sits inside a repo tracking ~ (as on
    # the dev box) -- proof the guard covers `ccs git` too.
    assert "not a git repository" in err or "HOME repo" in err


# -- auto_pull ----------------------------------------------------------------

def _enable_auto_pull(w):
    (w["user"] / "ccs-config.json").write_text(
        json.dumps({"auto_pull": True}), encoding="utf-8")


def test_auto_pull_fast_forwards_and_reports(fleet, capsys):
    _push_from_other(fleet)
    _enable_auto_pull(fleet)
    main(_ccs(fleet, "status"))
    out = capsys.readouterr().out
    assert "was 1 behind -- fast-forwarded" in out, out
    assert _head(fleet["co"]) == _head(fleet["other"])


def test_auto_pull_makes_the_drift_table_post_pull_truth(fleet, capsys):
    # Live already holds the other machine's content: after the ff the run
    # must end CLEAN -- pull and comparison happen in the SAME status.
    _push_from_other(fleet)
    _enable_auto_pull(fleet)
    (fleet["live"] / "F.md").write_bytes(V2)
    rc = main(_ccs(fleet, "status"))
    out = capsys.readouterr().out
    assert "status: clean" in out and rc == 0


def test_pull_flag_works_without_config(fleet, capsys):
    _push_from_other(fleet)
    rc = main(_ccs(fleet, "status", "--pull"))
    out = capsys.readouterr().out
    assert "fast-forwarded" in out
    assert _head(fleet["co"]) == _head(fleet["other"])


def test_no_pull_flag_beats_the_config(fleet, capsys):
    _push_from_other(fleet)
    _enable_auto_pull(fleet)
    before = _head(fleet["co"])
    main(_ccs(fleet, "status", "--no-pull"))
    out = capsys.readouterr().out
    assert "1 behind -- ccs git pull" in out
    assert _head(fleet["co"]) == before          # untouched


def test_auto_pull_refuses_divergence(fleet, capsys):
    _push_from_other(fleet)
    (fleet["co"] / "dotclaude" / "F.md").write_bytes(b"a\nb\nc\nlocal\n")
    _git(fleet["co"], "commit", "-qam", "local commit")
    before = _head(fleet["co"])
    rc = main(_ccs(fleet, "status", "--pull"))
    out = capsys.readouterr().out
    assert "fast-forward refused" in out and "diverged" in out
    assert _head(fleet["co"]) == before          # no merge, no rebase, no mess
    assert not (fleet["co"] / ".git" / "MERGE_HEAD").exists()
    assert rc == 1                               # behind is still drift


def test_no_fetch_suppresses_the_pull(fleet, capsys):
    _push_from_other(fleet)
    _enable_auto_pull(fleet)
    before = _head(fleet["co"])
    main(_ccs(fleet, "status", "--no-fetch"))
    assert _head(fleet["co"]) == before          # stale knowledge, no action


def test_dirty_file_refusal_carries_gits_first_line(fleet, capsys):
    # Mutation-sweep closure (M6, M8): the refusal REASON must survive into
    # the status output, and it must be git's FIRST line (the one that says
    # what happened), not its last ("Aborting").
    _push_from_other(fleet)
    (fleet["co"] / "dotclaude" / "F.md").write_bytes(b"dirty, uncommitted\n")
    main(_ccs(fleet, "status", "--pull"))
    out = capsys.readouterr().out
    assert "fast-forward refused" in out
    assert "local changes" in out       # git's first refusal line, verbatim
    assert (fleet["co"] / "dotclaude" / "F.md").read_bytes() == b"dirty, uncommitted\n"


def test_ff_update_without_upstream_refuses(fleet):
    # Mutation-sweep closure (M7): the primitive's guard must REFUSE, not
    # report a fast-forward that never happened.
    from dazzle_claude_config import gitops
    _git(fleet["co"], "branch", "--unset-upstream")
    repo = gitops.CheckoutRepo(fleet["co"])
    assert repo.ff_update() == (False, "no upstream configured")


def test_remote_url_comes_back_clean(fleet):
    # Mutation-sweep closure (M9): the primitive returns the URL with no
    # trailing newline -- consumers must not need a defensive strip.
    from dazzle_claude_config import gitops
    url = gitops.CheckoutRepo(fleet["co"]).remote_url()
    assert url and url == url.strip()


# -- the remote leg (#22) -----------------------------------------------------

def test_status_shows_a_labelled_remote_leg(fleet, capsys):
    main(_ccs(fleet, "status"))
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].startswith("remote")
    assert "main, in sync" in lines[0]
    assert lines[1].startswith("checkout") and "(on main)" in lines[1]


def test_remote_host_forms():
    assert remote_host("https://github.com/o/r.git") == "github.com/o/r"
    assert remote_host("git@github.com:o/r.git") == "github.com/o/r"
    assert remote_host("ssh://git@host:2222/o/r.git") == "host:2222/o/r"
    assert remote_host("https://user@host/o/r/") == "host/o/r"
    assert remote_host(None) is None
    # Brutal fallback: an unparseable URL comes back untouched, never guessed.
    assert remote_host(r"C:\somewhere\bare.git") == r"C:\somewhere\bare.git"


def test_humanize_remote_states():
    assert humanize_remote("## main...origin/main", fetched=True) == "main, in sync"
    assert humanize_remote("## main...origin/main", fetched=None) == \
        "main, in sync as last fetched"
    assert humanize_remote("## main...origin/main [behind 2]", fetched=True) == \
        "main, 2 behind -- ccs git pull"
    assert humanize_remote("## main...origin/main [ahead 1]", fetched=True) == \
        "main, 1 ahead -- ccs git push to share"
    assert "diverged" in humanize_remote(
        "## main...origin/main [ahead 1, behind 2]", fetched=True)
    assert humanize_remote("## main...origin/main [behind 2]", fetched=True,
                           pulled=(2, True, "")) == \
        "main, was 2 behind -- fast-forwarded"
    assert "fast-forward refused" in humanize_remote(
        "## main...origin/main [behind 2]", fetched=True,
        pulled=(2, False, "dirty"))
    assert humanize_remote("## main") == "main, no upstream configured"
    assert humanize_remote("## HEAD (no branch)") == \
        "detached HEAD (not on a branch)"
    assert "fetch failed" in humanize_remote("## main...origin/main",
                                             fetched=False, detail="offline")


# -- the help text says which way the verbs point (S4) ------------------------

def test_verb_help_states_the_direction():
    text = _build_parser().format_help()
    assert "live -> checkout" in text
    assert "checkout -> live" in text
    assert "run git in the checkout" in text
    assert "COLLECTS from a" in text and "APPLY to a box" in text
