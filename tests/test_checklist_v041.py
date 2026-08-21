"""Coverage gaps found while executing the v0.4.1 human checklist
(tests/checklists/v0.4.1__Feature__base-inference-status-attribution-direction-and-three-way-diff.md)
and probing the edge cases it lists under "EXTEND".

Report: tests/checklists/results/v0.4.1__Feature__tester-unbounded-run-01.md

Each test below documents a scenario the existing suite did not reach. The
sweep's one genuine finding (silent ambiguous-path resolution) was first
pinned as a characterisation test by the tester, then fixed in the same
release and the test flipped to assert the corrected behaviour.
"""
from __future__ import annotations

import subprocess as sp

from dazzle_claude_config import merge
from dazzle_claude_config.cli import main
from dazzle_claude_config.manifest import Manifest

from conftest import GIT_ID


def _git(co, *args: str) -> str:
    r = sp.run(["git", *GIT_ID, "-C", str(co), *args], capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout


# -- edge case (a): three commits, live == the OLDEST, not the middle --------

def test_infer_base_walks_past_two_intermediate_commits(tmp_path):
    """live matches the very first commit (c1); two more commits (c2, c3)
    exist on top of it in the checkout's history before HEAD.

    This is distinct from test_equal_ancestor_preferred_over_older_ancestor
    (test_base_inference.py), which has live match a MIDDLE commit while an
    OLDER one also exists -- a tie-break question. Here there is no tie: the
    only matching commit is two hops behind HEAD, so this instead checks that
    the history walk is not artificially shallow (e.g. capped at HEAD~1)."""
    co = tmp_path / "checkout"
    (co / "dotclaude").mkdir(parents=True)
    live = tmp_path / "live"
    live.mkdir()
    sp.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    (co / "ccs-manifest.json").write_text(
        '{"manifest_version":1,'
        '"territories":{"dotclaude":{"root_var":"CLAUDE_DIR","repo_dir":"dotclaude"}},'
        '"entries":[{"repo":"dotclaude/G.md","territory":"dotclaude",'
        '"target":"G.md","strategy":"copy"}]}', encoding="utf-8")

    (co / "dotclaude" / "G.md").write_text("v1\n", encoding="utf-8")
    _git(co, "add", "-A"); _git(co, "commit", "-qm", "c1")
    c1 = _git(co, "rev-parse", "--short", "HEAD").strip()
    (co / "dotclaude" / "G.md").write_text("v2\n", encoding="utf-8")
    _git(co, "add", "-A"); _git(co, "commit", "-qm", "c2")
    (co / "dotclaude" / "G.md").write_text("v3\n", encoding="utf-8")
    _git(co, "add", "-A"); _git(co, "commit", "-qm", "c3")

    (live / "G.md").write_text("v1\n", encoding="utf-8")  # == the OLDEST commit

    roots = {"CLAUDE_DIR": live, "USER_CLAUDE": tmp_path / "uc"}
    # one-sided: live holds nothing HEAD lacks, so the two-way guard must stay quiet
    assert merge.two_way_labels(Manifest.load(co), co, roots) == []
    found = merge.infer_base(co, "dotclaude/G.md", ours=b"v1\n",
                             theirs=(co / "dotclaude" / "G.md").read_bytes())
    assert found is not None
    assert found[1] == c1, (
        f"expected the evidence commit to be c1 ({c1}), the true two-hops-back "
        f"ancestor; got {found[1]}")


# -- finding (d): ccs diff <bare-filename> across two entries -----------------

def test_cli_diff_ambiguous_bare_filename_refuses_and_lists_candidates(tmp_path, capsys):
    """Two different manifest entries (s/, t/) each contain a file named
    SAME.md, both differing from live. `ccs diff SAME.md` (a bare filename)
    used to resolve to whichever entry came first in the manifest, silently
    (_resolve_pair returned on the first endswith hit) -- found by the v0.4.1
    checklist sweep, run-01 finding (d). A confident wrong answer at the path
    layer is the same failure class the release fixes at the base layer, so
    it is fixed here: refuse, list the candidates, exit 2; a qualified path
    resolves as before."""
    co = tmp_path / "checkout"
    (co / "dotclaude" / "s").mkdir(parents=True)
    (co / "dotclaude" / "t").mkdir()
    live = tmp_path / "live"
    (live / "s").mkdir(parents=True)
    (live / "t").mkdir()
    user = tmp_path / "user"
    user.mkdir()

    sp.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    (co / "ccs-manifest.json").write_text(
        '{"manifest_version":1,'
        '"territories":{"dotclaude":{"root_var":"CLAUDE_DIR","repo_dir":"dotclaude"}},'
        '"entries":['
        '{"repo":"dotclaude/s","territory":"dotclaude","target":"s","strategy":"copy"},'
        '{"repo":"dotclaude/t","territory":"dotclaude","target":"t","strategy":"copy"}'
        ']}', encoding="utf-8")
    (co / "dotclaude" / "s" / "SAME.md").write_text("s-version\n", encoding="utf-8")
    (co / "dotclaude" / "t" / "SAME.md").write_text("t-version\n", encoding="utf-8")
    _git(co, "add", "-A"); _git(co, "commit", "-qm", "seed")

    (live / "s" / "SAME.md").write_text("s-version-live-edit\n", encoding="utf-8")
    (live / "t" / "SAME.md").write_text("t-version-live-edit\n", encoding="utf-8")

    rc = main(["--checkout-dir", str(co), "--claude-dir", str(live),
               "--user-claude", str(user), "--no-color", "diff", "SAME.md"])
    out, err = capsys.readouterr()

    # Found silently picking s/ by the v0.4.1 checklist sweep (run-01, finding
    # (d)); fixed in the same release: refuse, list both, exit 2.
    assert rc == 2, (out, err)
    assert "ambiguous" in err and "2 files" in err
    assert "dotclaude/s/SAME.md" in err and "dotclaude/t/SAME.md" in err
    assert "s-version" not in out          # no diff was printed for either

    # A qualified path disambiguates.
    rc = main(["--checkout-dir", str(co), "--claude-dir", str(live),
               "--user-claude", str(user), "--no-color", "diff", "t/SAME.md"])
    out = capsys.readouterr().out
    assert rc == 1 and "t-version-live-edit" in out and "s-version" not in out


def test_cli_diff_bare_filename_matches_whole_components_only(tmp_path, capsys):
    """`SAME.md` must not match `notSAME.md`: the suffix match is on path
    components, so a bare filename never silently lands on a longer name."""
    co = tmp_path / "checkout"
    (co / "dotclaude" / "s").mkdir(parents=True)
    live = tmp_path / "live"
    (live / "s").mkdir(parents=True)
    user = tmp_path / "user"
    user.mkdir()
    sp.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    (co / "ccs-manifest.json").write_text(
        '{"manifest_version":1,'
        '"territories":{"dotclaude":{"root_var":"CLAUDE_DIR","repo_dir":"dotclaude"}},'
        '"entries":[{"repo":"dotclaude/s","territory":"dotclaude","target":"s","strategy":"copy"}]}',
        encoding="utf-8")
    (co / "dotclaude" / "s" / "notSAME.md").write_text("x\n", encoding="utf-8")
    _git(co, "add", "-A"); _git(co, "commit", "-qm", "seed")
    (live / "s" / "notSAME.md").write_text("y\n", encoding="utf-8")

    rc = main(["--checkout-dir", str(co), "--claude-dir", str(live),
               "--user-claude", str(user), "--no-color", "diff", "SAME.md"])
    _out, err = capsys.readouterr()
    assert rc == 2 and "no such file" in err
