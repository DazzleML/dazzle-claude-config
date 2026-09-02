"""One world, one actionable set: what `ccs status` calls yours-and-settled,
`ccs merge` must not plan.

Found live on 2026-09-02: a bare `ccs merge` on the real payload launched
BeyondCompare on `settings.local.json` -- a seed-if-absent file `ccs status`
had just reported as *seeded -- yours (kept until the payload's copy changes)*.
The window it opened held the empty committed seed as the base and the box's
whole permission list as live; a wrong-side save would have wiped it.

The two verbs share the comparison (`syncmap.diff_all`) and forked above it:
status routed seed entries through the seed-decision record (issue #27);
merge's candidate walk yielded every entry of any strategy and never read
that record. This is the third instance of the modularity design's G21 (one
comparison, two classifiers). The fix moved the state machine into
`seeddecisions.findings` and had `merge.run` read it -- the interim before
the design's unit 2.2 makes one `classify()` the implementation both verbs
filter over. These tests are AC-14, widened: status, the guard AND merge's
plan cannot disagree.

The first test was written as a strict xfail before the fix and went red
for the right reason (`--runxfail`: "would act on ['F.md', 'SEED.md']");
the marker came off with the fix.
"""
from __future__ import annotations

import subprocess as sp
from pathlib import Path

import pytest

from dazzle_claude_config import merge, seeddecisions
from dazzle_claude_config.cli import _norm_sha, _seed_findings, main
from dazzle_claude_config.manifest import Manifest

SEED = b"starter\n"


def _world(tmp_path: Path, *, decide: str | None = "until-changed",
           decided_against: bytes = SEED, move_seed: bool = False,
           customise: bool = True):
    """A checkout with one two-sided `copy` file and one `seed-if-absent`
    file, shaped by the keyword arguments:

      customise        live SEED.md differs from the seed (else it matches)
      decide           record keep(mode) for SEED.md, or None for no record
      decided_against  the seed bytes the decision was anchored to
      move_seed        commit a NEW seed after the decision (-> reopened)

    Every git call carries conftest's GIT_ID (commit.gpgsign=false): signing
    is on globally here."""
    from conftest import GIT_ID
    co = tmp_path / "checkout"; (co / "dotclaude").mkdir(parents=True)
    live = tmp_path / "live"; live.mkdir()
    uc = tmp_path / "userclaude"; uc.mkdir()

    def git(*a):
        r = sp.run(["git", *GIT_ID, "-C", str(co), *a], capture_output=True, text=True)
        assert r.returncode == 0, f"git {a}: {r.stderr}"

    sp.run(["git", "init", "-q", str(co)], capture_output=True)
    (co / "ccs-manifest.json").write_text(
        '{"manifest_version":1,'
        '"territories":{"dotclaude":{"root_var":"CLAUDE_DIR","repo_dir":"dotclaude"}},'
        '"entries":['
        '{"repo":"dotclaude/F.md","territory":"dotclaude","target":"F.md","strategy":"copy"},'
        '{"repo":"dotclaude/SEED.md","territory":"dotclaude","target":"SEED.md",'
        '"strategy":"seed-if-absent"}]}', encoding="utf-8")
    (co / "dotclaude/F.md").write_bytes(b"shared\ncommon\n")
    (co / "dotclaude/SEED.md").write_bytes(SEED)
    git("add", "-A"); git("commit", "-qm", "base")
    (co / "dotclaude/F.md").write_bytes(b"shared\ncommon\nTHEIRS\n")   # checkout moves F only
    git("add", "-A"); git("commit", "-qm", "theirs")
    if move_seed:
        (co / "dotclaude/SEED.md").write_bytes(SEED + b"the payload moved it\n")
        git("add", "-A"); git("commit", "-qm", "new seed")

    (live / "F.md").write_bytes(b"shared\ncommon\nOURS\n")            # live moves F too: two-sided
    (live / "SEED.md").write_bytes(SEED + b"mine, after delivery\n" if customise else SEED)
    if decide:
        seeddecisions.keep("SEED.md", decide, _norm_sha(decided_against), uc)

    roots = {"CLAUDE_DIR": live, "USER_CLAUDE": uc}
    return Manifest.load(co), co, roots, uc


def _status_states(manifest, co, roots, uc) -> dict[str, str]:
    findings, errors = _seed_findings(manifest, co, roots, None, frozenset(), uc)
    assert errors == []
    return {target: state for target, state, _live, _repo in findings}


def _would_do(manifest, co, roots, **kw):
    """What `ccs merge` would act on, through the same entry point the CLI
    uses. dry_run returns res.planned without launching or writing back, and
    with the internal stage run() always passes -- the path a bare plan()
    without a stage silently skips (a first cut of this file learned that)."""
    res = merge.run(manifest, co, roots, dry_run=True, launch_tool=False, **kw)
    return {i.label for i in res.planned}, {i.label: i.reason for i in res.refused}


# -- the property -----------------------------------------------------------

def test_a_kept_seed_is_not_in_what_merge_would_do(tmp_path):
    manifest, co, roots, uc = _world(tmp_path)
    assert _status_states(manifest, co, roots, uc) == {"SEED.md": "kept-current"}

    planned, refused = _would_do(manifest, co, roots)
    assert planned == {"F.md"}, planned
    assert "SEED.md" in refused and "seeded and kept-current" in refused["SEED.md"]
    assert "ccs merge SEED.md" in refused["SEED.md"]      # the way to override, named


def test_a_kept_always_seed_is_refused_too(tmp_path):
    manifest, co, roots, uc = _world(tmp_path, decide="always")
    assert _status_states(manifest, co, roots, uc) == {"SEED.md": "kept-always"}
    planned, refused = _would_do(manifest, co, roots)
    assert planned == {"F.md"} and "seeded and kept-always" in refused["SEED.md"]


def test_a_seed_that_matches_never_reaches_merge_at_all(tmp_path):
    """`matches` is settled upstream of the refusal: a live file identical to
    its seed is identical to HEAD, and plan() drops it before any policy
    runs. Neither planned nor refused -- and that is the right silence."""
    manifest, co, roots, uc = _world(tmp_path, decide=None, customise=False)
    assert _status_states(manifest, co, roots, uc) == {"SEED.md": "matches"}
    planned, refused = _would_do(manifest, co, roots)
    assert planned == {"F.md"} and "SEED.md" not in refused


# -- the two states that still ask stay mergeable ----------------------------

def test_an_open_seed_no_decision_recorded_is_still_planned(tmp_path):
    manifest, co, roots, uc = _world(tmp_path, decide=None)
    assert _status_states(manifest, co, roots, uc) == {"SEED.md": "open"}
    planned, _ = _would_do(manifest, co, roots)
    assert planned == {"F.md", "SEED.md"}


def test_a_reopened_seed_the_payload_moved_since_the_decision_is_still_planned(tmp_path):
    manifest, co, roots, uc = _world(tmp_path, move_seed=True)   # decided against the OLD seed
    assert _status_states(manifest, co, roots, uc) == {"SEED.md": "reopened"}
    planned, _ = _would_do(manifest, co, roots)
    assert planned == {"F.md", "SEED.md"}


# -- naming the file is consent ----------------------------------------------

def test_an_explicit_scope_lifts_the_refusal(tmp_path):
    manifest, co, roots, uc = _world(tmp_path)
    planned, refused = _would_do(manifest, co, roots, only="dotclaude/SEED.md")
    assert planned == {"SEED.md"} and refused == {}


def test_the_positional_path_lifts_it_from_the_cli_too(tmp_path, capsys):
    manifest, co, roots, uc = _world(tmp_path)
    rc = main(["--checkout-dir", str(co), "--claude-dir", str(roots["CLAUDE_DIR"]),
               "--user-claude", str(uc), "--no-color", "--no-fetch",
               "merge", "--dry-run", "--no-launch", "SEED.md"])
    out, _err = capsys.readouterr()
    assert rc in (0, 1), out
    assert "would merge: SEED.md" in out and "refused" not in out


def test_a_bare_run_says_refused_and_why_on_the_console(tmp_path, capsys):
    manifest, co, roots, uc = _world(tmp_path)
    rc = main(["--checkout-dir", str(co), "--claude-dir", str(roots["CLAUDE_DIR"]),
               "--user-claude", str(uc), "--no-color", "--no-fetch",
               "merge", "--dry-run", "--no-launch"])
    out, _err = capsys.readouterr()
    assert rc in (0, 1), out
    assert "refused SEED.md -- seeded and kept-current" in out
    assert "would merge: F.md" in out and "would merge: SEED.md" not in out
