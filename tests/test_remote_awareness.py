"""Remote awareness: `status` fetches before it calls the checkout "in sync".

Design: 2026-08-21__07-45-32__dev-workflow-process__ccs-remote-awareness-fetch-before-you-trust-in-sync.md
(personal vault) and its Addendum 1 (apply warns, does not refuse).

Before this, the branch line came from `git status -sb`, which compares
against whatever the LAST fetch left behind; "in sync with origin/main" was
true on the first round trip only because the operator had fetched by hand.
These tests build a bare "GitHub" and two clones so the upstream can really
move, and check every acceptance check the design named that a unit test
can reach (AC-8, the credential-prompt hang, and AC-9, measured latency, are
on the human checklist).
"""
from __future__ import annotations

import json
import subprocess as sp
from pathlib import Path

import pytest

from dazzle_claude_config import gitops
from dazzle_claude_config.cli import main
from dazzle_claude_config.render import humanize_branch

from conftest import GIT_ID

# Multi-line content on purpose: the base finder breaks exact distance ties
# AGAINST the candidate equal to the checkout (a revert must not win by an
# immunity it did not earn), so one-line fixtures where every version is one
# edit from every other read as two-sided. Real files are not one line.
V0 = b"a\nb\n"
V1 = b"a\nb\nc\n"                 # what both machines last agreed on
V2 = b"a\nb\nc\nfrom-the-other-machine\n"
V1B = b"a\nb\nc\nlocal-commit\n"
LIVE_EDIT = b"a\nb\nc\nlive-edit\n"

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
    """A bare remote, a checkout that tracks it, a second clone that can push
    to it (the "other machine"), and a live tree equal to the checkout."""
    bare = tmp_path / "remote.git"
    sp.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    other = tmp_path / "other"
    sp.run(["git", "-c", "core.autocrlf=false", "clone", "-q", str(bare), str(other)], check=True, capture_output=True)
    (other / "dotclaude").mkdir()
    (other / "ccs-manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    # Two commits, not one: with a single commit in history the base finder
    # refuses on purpose (HEAD as the sole candidate is indistinguishable from
    # adoption), which would mask what these tests are about.
    (other / "dotclaude" / "F.md").write_bytes(V0)
    _git(other, "add", "-A"); _git(other, "commit", "-qm", "seed v0")
    (other / "dotclaude" / "F.md").write_bytes(V1)
    _git(other, "commit", "-qam", "seed v1"); _git(other, "push", "-q", "-u", "origin", "main")
    co = tmp_path / "checkout"
    sp.run(["git", "-c", "core.autocrlf=false", "clone", "-q", str(bare), str(co)], check=True, capture_output=True)
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


# -- AC-1: a real fetch finds the other machine's push ------------------------

def test_status_reports_behind_after_the_other_machine_pushes(fleet, capsys):
    _push_from_other(fleet)
    rc = main(_ccs(fleet, "status"))
    out = capsys.readouterr().out
    assert "your checkout is 1 behind -- ccs status --pull" in out, out   # names the side AND the verb
    assert "in sync" not in out
    assert "--pull" in out                       # the verdict names the verb that acts
    assert rc == 1                               # behind is drift, even with live == checkout


def test_status_in_sync_only_after_a_successful_fetch(fleet, capsys):
    rc = main(_ccs(fleet, "status"))
    out = capsys.readouterr().out
    assert "main, in sync" in out                  # the remote leg (#22 wording)
    assert "as last fetched" not in out and "unknown" not in out
    assert "status: clean" in out and rc == 0


# -- AC-2: unreachable remote -> the negative is withheld ---------------------

def test_status_withholds_in_sync_when_fetch_fails(fleet, capsys):
    _git(fleet["co"], "remote", "set-url", "origin", "http://127.0.0.1:9/nowhere.git")
    rc = main(_ccs(fleet, "status"))
    out = capsys.readouterr().out
    assert "pull status unknown -- fetch failed:" in out, out
    assert "in sync" not in out
    assert "as last fetched" in out
    assert rc == 0                               # live == checkout; unknown is not drift


# -- AC-3: --no-fetch / fetch:false -> no network, honest wording -------------

def test_no_fetch_flag_skips_the_network_and_says_so(fleet, capsys, monkeypatch):
    _push_from_other(fleet)
    calls = []
    real = gitops.CheckoutRepo.fetch
    monkeypatch.setattr(gitops.CheckoutRepo, "fetch",
                        lambda self, timeout=15: calls.append(timeout) or real(self, timeout))
    rc = main(_ccs(fleet, "status", "--no-fetch"))
    out = capsys.readouterr().out
    assert calls == []                           # never fetched
    assert "in sync as last fetched" in out        # the stale tracking ref, labelled (#22)
    assert rc == 0


def test_fetch_false_in_config_skips_too(fleet, capsys, monkeypatch):
    (fleet["user"] / "ccs-config.json").write_text('{"fetch": false}', encoding="utf-8")
    monkeypatch.setattr(gitops.CheckoutRepo, "fetch",
                        lambda self, timeout=15: pytest.fail("fetch ran despite fetch:false"))
    main(_ccs(fleet, "status"))
    assert "as last fetched" in capsys.readouterr().out


# -- AC-4: read-only -- only refs/remotes/* may change ------------------------

def test_fetch_changes_nothing_but_remote_tracking_refs(fleet, capsys):
    co = fleet["co"]
    (co / "dotclaude" / "scratch.txt").write_bytes(b"uncommitted\n")   # a dirty worktree, too
    _push_from_other(fleet)
    before = (_git(co, "rev-parse", "HEAD"), _git(co, "write-tree"),
              _git(co, "status", "--porcelain"), _git(co, "rev-parse", "origin/main"))
    main(_ccs(fleet, "status"))
    capsys.readouterr()
    after = (_git(co, "rev-parse", "HEAD"), _git(co, "write-tree"),
             _git(co, "status", "--porcelain"), _git(co, "rev-parse", "origin/main"))
    assert before[:3] == after[:3], "HEAD, index, or worktree moved"
    assert before[3] != after[3], "origin/main did not advance -- no fetch happened"


# -- AC-5a/b: the verbs warn by default, refuse only under require_current ----

def test_apply_behind_warns_and_proceeds(fleet, capsys):
    _push_from_other(fleet)
    # the checkout is ahead of live on its OWN history (live == v1, HEAD v1b),
    # and 1 behind the remote (v2) at the same time
    (fleet["co"] / "dotclaude" / "F.md").write_bytes(V1B)
    _git(fleet["co"], "commit", "-qam", "local commit")
    rc = main(_ccs(fleet, "apply"))
    out = capsys.readouterr().out
    assert "note: checkout is 1 behind origin/main -- applying what is here" in out, out
    assert rc == 0
    assert (fleet["live"] / "F.md").read_bytes() == V1B


def test_apply_behind_refuses_under_require_current(fleet, capsys):
    _push_from_other(fleet)
    (fleet["co"] / "dotclaude" / "F.md").write_bytes(V1B)
    _git(fleet["co"], "commit", "-qam", "local commit")
    rc = main(_ccs(fleet, "apply", "--require-current"))
    out = capsys.readouterr().out
    assert "REFUSING: checkout is 1 behind origin/main -- git pull first" in out, out
    assert rc == 1
    assert (fleet["live"] / "F.md").read_bytes() == V1      # untouched


def test_collect_behind_warns_with_its_own_advice(fleet, capsys):
    _push_from_other(fleet)
    (fleet["live"] / "F.md").write_bytes(LIVE_EDIT)        # live ahead
    rc = main(_ccs(fleet, "collect"))
    out = capsys.readouterr().out
    assert "note: checkout is 1 behind origin/main -- collecting onto a stale base; git pull before you push" in out, out
    assert rc == 0
    assert (fleet["co"] / "dotclaude" / "F.md").read_bytes() == LIVE_EDIT


def test_require_current_from_config_file(fleet, capsys):
    _push_from_other(fleet)
    (fleet["user"] / "ccs-config.json").write_text('{"require_current": true}', encoding="utf-8")
    (fleet["live"] / "F.md").write_bytes(LIVE_EDIT)
    rc = main(_ccs(fleet, "collect"))
    assert rc == 1 and "REFUSING" in capsys.readouterr().out
    assert (fleet["co"] / "dotclaude" / "F.md").read_bytes() == V1


# -- AC-5c / AC-6: a FAILED fetch never refuses, even when strict -------------

def test_failed_fetch_proceeds_even_under_require_current(fleet, capsys):
    _git(fleet["co"], "remote", "set-url", "origin", "http://127.0.0.1:9/nowhere.git")
    (fleet["live"] / "F.md").write_bytes(LIVE_EDIT)
    rc = main(_ccs(fleet, "collect", "--require-current"))
    out = capsys.readouterr().out
    assert "note: could not fetch origin/main" in out and "proceeding" in out, out
    assert rc == 0
    assert (fleet["co"] / "dotclaude" / "F.md").read_bytes() == LIVE_EDIT


# -- AC-7: no upstream / plain directory: no fetch, wording unchanged ---------

def test_no_upstream_means_no_fetch_and_old_wording(tmp_path, capsys, monkeypatch):
    co = tmp_path / "co"; (co / "dotclaude").mkdir(parents=True)
    sp.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    (co / "ccs-manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (co / "dotclaude" / "F.md").write_bytes(V1)
    _git(co, "add", "-A"); _git(co, "commit", "-qm", "seed")
    live = tmp_path / "live"; live.mkdir(); (live / "F.md").write_bytes(V1)
    user = tmp_path / "user"; user.mkdir()
    ran = []
    monkeypatch.setattr(sp, "run", (lambda orig: (lambda *a, **k: (ran.append(a[0]) if "fetch" in a[0] else None) or orig(*a, **k)))(sp.run))
    rc = main(["--checkout-dir", str(co), "--claude-dir", str(live), "--user-claude", str(user),
               "--no-color", "status"])
    out = capsys.readouterr().out
    assert "main, no upstream configured" in out   # remote leg wording (#22)
    assert ran == [] and rc == 0


# -- the primitive, on its own ------------------------------------------------

def test_fetch_timeout_is_reported_not_raised(fleet, monkeypatch):
    repo = gitops.CheckoutRepo(fleet["co"])

    def slow(*a, **k):
        raise sp.TimeoutExpired(cmd="git fetch", timeout=k.get("timeout"))
    monkeypatch.setattr(gitops.CheckoutRepo, "upstream", lambda self: "origin/main")
    monkeypatch.setattr(gitops.subprocess, "run", slow)
    ok, detail = repo.fetch(timeout=3)
    assert ok is False and detail == "timed out after 3s"


def test_fetch_env_is_non_interactive(fleet, monkeypatch):
    repo = gitops.CheckoutRepo(fleet["co"])
    seen = {}

    def spy(cmd, **k):
        seen["env"] = k.get("env"); seen["cmd"] = cmd
        class R: returncode, stdout, stderr = 0, "", ""
        return R()
    monkeypatch.setattr(gitops.CheckoutRepo, "upstream", lambda self: "origin/main")
    monkeypatch.setattr(gitops.subprocess, "run", spy)
    assert repo.fetch() == (True, "")
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert seen["env"]["GCM_INTERACTIVE"] == "never"
    assert seen["cmd"][:3] == ["git", "fetch", "origin"] and "--prune" in seen["cmd"]


def test_humanize_branch_states():
    assert humanize_branch("## main...origin/main") == "on main, in sync with origin/main"
    assert humanize_branch("## main...origin/main", fetched=None) == \
        "on main, in sync with origin/main as last fetched"
    assert humanize_branch("## main...origin/main [behind 2]", fetched=True) == \
        "on main, 2 behind vs origin/main -- git pull"
    assert humanize_branch("## main...origin/main [behind 2]", fetched=None) == \
        "on main, 2 behind vs origin/main as last fetched -- git fetch to confirm"
    assert humanize_branch("## main...origin/main", fetched=False, detail="could not resolve host") == \
        "on main, pull status unknown -- fetch failed: could not resolve host (vs origin/main as last fetched)"
    assert humanize_branch("## main...origin/main [gone]", fetched=True) == \
        "on main, upstream origin/main no longer exists on the remote"
    assert humanize_branch("## main", fetched=True) == "on main, no upstream configured"


# -- the summary line, for a checkout that is AHEAD ---------------------------
#
# The summary already refused to say "clean" while the checkout was BEHIND.
# It said it happily while the checkout was AHEAD, and that is the worse of
# the two to stay quiet about: behind means the work arrives on your next
# pull, ahead means it exists on exactly one machine. Found by the maintainer
# reading real output, not by any of these tests.

def _commit_locally(w, content=b"local only\n"):
    """Advance the CHECKOUT without pushing -- work on one machine only."""
    (w["co"] / "dotclaude" / "F.md").write_bytes(content)
    _git(w["co"], "commit", "-qam", "work that exists here and nowhere else")
    (w["live"] / "F.md").write_bytes(content)     # keep live == checkout


def test_a_checkout_that_is_AHEAD_is_not_called_clean(fleet, capsys):
    _commit_locally(fleet)
    main(_ccs(fleet, "status"))
    out = capsys.readouterr().out
    assert "status: clean" not in out, (
        "a commit that exists on exactly one machine is not 'clean'\n" + out)
    assert "live matches the checkout" in out, (
        "the live-vs-checkout fact is still true and must still be stated")
    assert "not on origin/main yet" in out, out
    assert "ccs git push" in out, "the summary must name the verb that acts"


def test_the_ahead_summary_counts_in_english(fleet, capsys):
    """One commit is 'commit ... is', two are 'commits ... are'.

    Written both ways deliberately. Four times this release a plural was
    fixed on the line somebody was looking at and left wrong on the next, and
    a test that pins only the singular is exactly how that keeps happening.
    """
    _commit_locally(fleet, b"one\n")
    main(_ccs(fleet, "status"))
    out = capsys.readouterr().out
    assert "1 commit here is not on" in out, out

    _commit_locally(fleet, b"two\n")
    main(_ccs(fleet, "status"))
    out = capsys.readouterr().out
    assert "2 commits here are not on" in out, out


def test_a_DIVERGED_checkout_is_not_told_to_fast_forward(fleet, capsys):
    """Both sides moved, so `--pull` cannot help -- it fast-forwards only and
    will refuse. Recommending it would send the reader to a command that
    cannot work, which is the same class of defect as `apply` printing
    `ccs collect` for a stale file."""
    _push_from_other(fleet)          # remote advances
    _commit_locally(fleet)           # and so does the checkout
    main(_ccs(fleet, "status"))
    out = capsys.readouterr().out
    assert "diverged" in out, out
    assert "status --pull" not in out, (
        "a fast-forward cannot resolve a divergence; do not recommend it\n" + out)
    assert "ccs git" in out, "point at the checkout, where it can be resolved"


def test_in_sync_and_matching_is_still_plainly_clean(fleet, capsys):
    """The guard on the three tests above: none of them may have made the
    ordinary case noisy. Nothing pending anywhere is still one green line."""
    rc = main(_ccs(fleet, "status"))
    out = capsys.readouterr().out
    assert "status: clean" in out, out
    assert rc == 0
