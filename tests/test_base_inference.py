"""Base inference: does ccs find the right ancestor when one side is unchanged?

Written RED on 2026-08-21 against 84026c8, before the fix, to verify the
diagnosis in:
  private/claude/2026-08-21__03-27-24__dev-workflow-process__status-mislabels-one-sided-drift-as-two-sided.md

Each test states the failure it predicts on the unfixed code. If a test
passes unexpectedly, the diagnosis is wrong and the fix should not proceed.

Real-world trigger: `ccs status` on the dev machine labelled 22 one-sided files as
"differs on both sides". Live equalled an ancestor commit byte-for-byte;
`infer_base` skips any candidate equal to a side ("degenerate"), so it either
found no base (-> flagged) or fell through to an OLDER ancestor (-> flagged).

The existing test_two_way_labels_ignores_one_sided_drift is misnamed: it sets
live == HEAD, which is "in sync", not one-sided. The one-sided case -- live
== base while HEAD moved on -- is what these tests add.
"""
from __future__ import annotations

from dazzle_claude_config import merge
from dazzle_claude_config.cli import main
from dazzle_claude_config.manifest import Manifest

from test_merge import _two_way_repo


# -- one-sided, checkout ahead (the 22-file case) -----------------------------

def test_one_sided_checkout_ahead_is_not_flagged(tmp_path):
    """live == the BASE commit; HEAD added a line. Nothing on the live side is
    unique, so a one-way apply loses nothing: must NOT be labelled two-sided.

    Predicted on 84026c8: FAILS -- returns ['F.md']. infer_base skips the
    base commit because cand == ours, leaving no candidates -> None -> flagged
    ("no base -> cannot prove it is safe")."""
    co, roots, _run = _two_way_repo(tmp_path)
    base_bytes = b"shared\ncommon\n"
    (roots["CLAUDE_DIR"] / "F.md").write_bytes(base_bytes)
    assert merge.two_way_labels(Manifest.load(co), co, roots) == []


def test_infer_base_returns_the_ancestor_equal_to_ours(tmp_path):
    """The commit whose content equals ours IS the ancestor. It must be
    returned, not skipped as degenerate.

    Predicted on 84026c8: FAILS -- returns None."""
    co, roots, _run = _two_way_repo(tmp_path)
    base_bytes = b"shared\ncommon\n"
    head_bytes = (co / "dotclaude/F.md").read_bytes()
    found = merge.infer_base(co, "dotclaude/F.md", ours=base_bytes, theirs=head_bytes)
    assert found is not None, "ancestor equal to ours was discarded"
    assert merge._normalize_eol(found[0]) == base_bytes


def test_equal_ancestor_preferred_over_older_ancestor(tmp_path):
    """The CLAUDE.md route: live == a MIDDLE commit; an older commit also
    exists. The equal one must win, or live's zero changes look like changes
    since the older commit.

    Predicted on 84026c8: FAILS -- the equal commit is skipped, the older one
    is chosen, base != ours -> flagged ['F.md']."""
    co, roots, run = _two_way_repo(tmp_path)
    # history is now: base -> "theirs adds". Add one more commit so the
    # middle one is the true ancestor of live.
    mid = (co / "dotclaude/F.md").read_bytes()                  # base + THEIRS
    (co / "dotclaude/F.md").write_bytes(mid + b"LATER\n")
    run("add", "-A"); run("commit", "-qm", "later")
    (roots["CLAUDE_DIR"] / "F.md").write_bytes(mid)             # live == middle
    assert merge.two_way_labels(Manifest.load(co), co, roots) == []
    found = merge.infer_base(co, "dotclaude/F.md", ours=mid,
                             theirs=(co / "dotclaude/F.md").read_bytes())
    assert found is not None and merge._normalize_eol(found[0]) == mid


# -- mirror: one-sided, live ahead --------------------------------------------

def test_one_sided_live_ahead_is_not_flagged(tmp_path):
    """HEAD's content equals an OLDER commit (a revert); live added a line.
    Mirror of the above: theirs unchanged since that ancestor.

    Predicted on 84026c8: FAILS -- the commit equal to theirs is skipped;
    the remaining candidate (base+THEIRS) equals neither side -> flagged."""
    co, roots, run = _two_way_repo(tmp_path)
    base_bytes = b"shared\ncommon\n"
    (co / "dotclaude/F.md").write_bytes(base_bytes)             # revert to base
    run("add", "-A"); run("commit", "-qm", "revert to base")
    (roots["CLAUDE_DIR"] / "F.md").write_bytes(base_bytes + b"OURS\n")
    assert merge.two_way_labels(Manifest.load(co), co, roots) == []


def test_one_sided_live_deleted_lines_not_flagged(tmp_path):
    """AC-mirror (the second machine's finding, 2026-08-21): checkout unchanged, live
    DELETED >= 3 lines. An older commit has HEAD's exact content, so a
    candidate equal to theirs exists.

    This is the case the previous mirror test misses: it only ADDS a line on
    the live side, so the phantom guard never has deletions to count.

    base_phantom_ratio is directional -- it counts base->ours pure deletions
    and asks whether theirs retains them. When the candidate IS theirs, every
    live-side deletion is retained by definition: ratio 1.0, n >= 3,
    rejected as a "sibling". So merely removing the equality skip does not
    fix this direction; equality must be accepted BEFORE the phantom check.

    Predicted on 84026c8: FAILS (equal candidate skipped; the remaining
    candidate is chosen; base equals neither side -> flagged).
    Predicted on S1-as-first-written (skip removed, phantom kept): STILL
    FAILS (equal candidate rejected as sibling). Verified via mutant M1."""
    co, roots, run = _two_way_repo(tmp_path)
    six = b"l1\nl2\nl3\nl4\nl5\nl6\n"
    three = b"l1\nl2\nl3\n"
    (co / "dotclaude/F.md").write_bytes(six)
    run("add", "-A"); run("commit", "-qm", "c1 six lines")
    (co / "dotclaude/F.md").write_bytes(six + b"X\n")
    run("add", "-A"); run("commit", "-qm", "c2 add X")
    (co / "dotclaude/F.md").write_bytes(six)
    run("add", "-A"); run("commit", "-qm", "c3 revert")
    (roots["CLAUDE_DIR"] / "F.md").write_bytes(three)           # live deleted l4-l6
    assert merge.two_way_labels(Manifest.load(co), co, roots) == []
    found = merge.infer_base(co, "dotclaude/F.md", ours=three, theirs=six)
    assert found is not None and merge._normalize_eol(found[0]) == six


# -- must keep passing: genuine two-sided, and the synthetic gate case ---------

def test_genuine_two_sided_still_flagged(tmp_path):
    """Regression fence for the fix: live has OURS, HEAD has THEIRS, base has
    neither. Must stay flagged. (Same scenario as the existing
    test_two_way_labels_flags_a_file_both_sides_changed; duplicated here so
    this file is self-contained as the fix's acceptance set.)"""
    co, roots, _run = _two_way_repo(tmp_path)
    assert merge.two_way_labels(Manifest.load(co), co, roots) == ["F.md"]


# -- the second bug: ccs diff <path> ------------------------------------------

def test_cli_diff_path_prints_instead_of_crashing(env, capsys):
    """`ccs diff <path>` must print the difference, not raise.

    Predicted on 84026c8: FAILS -- NameError: _print_file_diff is not defined
    (cli.py:577; the function was never written, and no test reaches it)."""
    claude, user, checkout, _, _ = env
    (claude / "CLAUDE.md").write_text("# global memory v2\n", encoding="utf-8")
    rc = main(["--checkout-dir", str(checkout), "--claude-dir", str(claude),
               "--user-claude", str(user), "--no-color", "diff", "dotclaude/CLAUDE.md"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "-# global memory v1" in out and "+# global memory v2" in out


def test_cli_diff_path_identical_after_sync(env, capsys):
    """Right after a merge this is the normal case and used to read 'no match'."""
    claude, user, checkout, _, _ = env
    rc = main(["--checkout-dir", str(checkout), "--claude-dir", str(claude),
               "--user-claude", str(user), "--no-color", "diff", "dotclaude/CLAUDE.md"])
    assert rc == 0
    assert "identical" in capsys.readouterr().out


def test_cli_diff_path_unknown_is_an_error(env, capsys):
    claude, user, checkout, _, _ = env
    rc = main(["--checkout-dir", str(checkout), "--claude-dir", str(claude),
               "--user-claude", str(user), "--no-color", "diff", "dotclaude/nope.md"])
    captured = capsys.readouterr()          # readouterr() drains; call it once
    assert rc == 2
    assert "no such" in (captured.out + captured.err).lower()


def test_cli_diff_path_survives_a_cp1252_console(env, monkeypatch):
    """Config files carry emoji; a Windows console is cp1252. `diff <path>`
    prints content, so without an errors= policy it crashed with
    UnicodeEncodeError on a read-only verb (measured on commands/ask.md,
    2026-08-21). Must degrade, never die."""
    import io, sys
    claude, user, checkout, _, _ = env
    (claude / "CLAUDE.md").write_text("# memory \U0001f4a1 tip\n", encoding="utf-8")
    buf = io.BytesIO()
    narrow = io.TextIOWrapper(buf, encoding="cp1252", errors="strict", newline="")
    monkeypatch.setattr(sys, "stdout", narrow)
    rc = main(["--checkout-dir", str(checkout), "--claude-dir", str(claude),
               "--user-claude", str(user), "--no-color", "diff", "dotclaude/CLAUDE.md"])
    narrow.flush()
    assert rc == 1
    assert b"memory ? tip" in buf.getvalue()      # degraded, not crashed


# -- direction-aware one-way verbs (2026-08-21) ---------------------------------
#
# A one-sided file is safe for ONE verb, not both. Live-ahead: nothing to apply
# (apply would revert the user's edits). Checkout-ahead: nothing to collect
# (collect would undo the other machine's work). Measured on a real payload:
# 3 live-ahead and 21 checkout-ahead files at once -- either verb alone would
# have clobbered one set while the two-way guard, correctly, stayed silent.

def _live_ahead_and_checkout_ahead(tmp_path):
    """Two file entries in one manifest: A is checkout-ahead (live == older
    commit), B is live-ahead (checkout untouched since its commit)."""
    import subprocess as sp
    from conftest import GIT_ID
    co = tmp_path / "checkout"; (co / "dotclaude").mkdir(parents=True)
    live = tmp_path / "live"; live.mkdir()
    user = tmp_path / "user"; user.mkdir()

    def run(*a):
        r = sp.run(["git", *GIT_ID, "-C", str(co), *a], capture_output=True, text=True)
        assert r.returncode == 0, f"git {a}: {r.stderr}"

    sp.run(["git", "init", "-q", "-b", "main", str(co)], capture_output=True)
    (co / "ccs-manifest.json").write_text(
        '{"manifest_version":1,'
        '"territories":{"dotclaude":{"root_var":"CLAUDE_DIR","repo_dir":"dotclaude"}},'
        '"entries":[{"repo":"dotclaude/A.md","territory":"dotclaude","target":"A.md","strategy":"copy"},'
        '{"repo":"dotclaude/B.md","territory":"dotclaude","target":"B.md","strategy":"copy"}]}',
        encoding="utf-8")
    (co / "dotclaude/A.md").write_bytes(b"a1\na2\n")
    (co / "dotclaude/B.md").write_bytes(b"b1\nb2\n")
    run("add", "-A"); run("commit", "-qm", "seed")
    (co / "dotclaude/A.md").write_bytes(b"a1\na2\nA-NEW\n")     # checkout moves A
    run("add", "-A"); run("commit", "-qm", "A advances")
    (live / "A.md").write_bytes(b"a1\na2\n")                    # live == seed for A
    (live / "B.md").write_bytes(b"b1\nb2\nB-LOCAL\n")           # live moves B
    return co, live, user


def test_apply_skips_live_ahead_files(tmp_path, capsys):
    co, live, user = _live_ahead_and_checkout_ahead(tmp_path)
    rc = main(["--checkout-dir", str(co), "--claude-dir", str(live),
               "--user-claude", str(user), "--no-color", "apply"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert (live / "A.md").read_bytes() == b"a1\na2\nA-NEW\n"     # checkout-ahead: applied
    assert (live / "B.md").read_bytes() == b"b1\nb2\nB-LOCAL\n"   # live-ahead: untouched
    assert "skipped B.md" in out and "live is ahead" in out


def test_collect_skips_checkout_ahead_files(tmp_path, capsys):
    co, live, user = _live_ahead_and_checkout_ahead(tmp_path)
    rc = main(["--checkout-dir", str(co), "--claude-dir", str(live),
               "--user-claude", str(user), "--no-color", "collect"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert (co / "dotclaude/A.md").read_bytes() == b"a1\na2\nA-NEW\n"   # checkout-ahead: untouched
    assert (co / "dotclaude/B.md").read_bytes() == b"b1\nb2\nB-LOCAL\n" # live-ahead: collected
    assert "skipped dotclaude/A.md" in out and "checkout is ahead" in out


def test_status_attribution_agrees_with_the_guard(tmp_path, capsys):
    """status and the two-way guard must never disagree: both go through
    infer_base. Here nothing is two-sided, so the guard is empty and status
    must not say 'BOTH sides' -- it used to, for every file that merely
    differed (cli.py _print_entry_files, pre-2026-08-21)."""
    co, live, user = _live_ahead_and_checkout_ahead(tmp_path)
    assert merge.two_way_labels(Manifest.load(co), co,
                                {"CLAUDE_DIR": live, "USER_CLAUDE": user}) == []
    rc = main(["--checkout-dir", str(co), "--claude-dir", str(live),
               "--user-claude", str(user), "--no-color", "status", "--long"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "BOTH sides" not in out
    assert "checkout ahead; live ==" in out      # A, with its evidence sha
    assert "live ahead; checkout ==" in out      # B


# -- mutation sweep, v0.4.1 (tests/mutation/v0.4.1__sweep__base-inference.md) --
# Each test below killed a survivor of the fresh-context sweep: behaviour the
# docstrings promised and the suite, until then, merely executed.

def test_tie_between_head_and_an_ancestor_goes_to_the_ancestor(tmp_path):
    """Rule 3, strict win (simulator scenario E; sweep mutants M5 and M7).

    c1 holds six lines. The checkout REPLACED l4-l6 (HEAD); live DELETED
    l4-l6. Live is distance 3 from both c1 and HEAD -- an exact tie. HEAD is
    exempt from the phantom check only because it equals theirs, and that
    immunity must not also win it the tie: c1 is the true ancestor, both
    sides moved away from it, and the file is two-sided. Letting HEAD win
    (M5) reads as "live ahead" and collect overwrites the checkout's
    replacement. Turning the phantom gate's AND into OR (M7) rejects c1 for
    its three deletions even though theirs retains none of them -> no base."""
    co, roots, run = _two_way_repo(tmp_path)
    six = b"l1\nl2\nl3\nl4\nl5\nl6\n"
    (co / "dotclaude/F.md").write_bytes(six)
    run("add", "-A"); run("commit", "-qm", "c1 six lines")
    (co / "dotclaude/F.md").write_bytes(b"l1\nl2\nl3\nR4\nR5\nR6\n")   # checkout replaced
    run("add", "-A"); run("commit", "-qm", "c2 replace l4-l6")
    live = b"l1\nl2\nl3\n"                                            # live deleted
    (roots["CLAUDE_DIR"] / "F.md").write_bytes(live)
    found = merge.infer_base(co, "dotclaude/F.md", ours=live,
                             theirs=(co / "dotclaude/F.md").read_bytes())
    assert found is not None, "the tie must resolve to c1, not to 'no base'"
    assert merge._normalize_eol(found[0]) == six, "HEAD won the tie by an immunity it did not earn"
    assert merge.two_way_labels(Manifest.load(co), co, roots) == ["F.md"]


def test_replaced_lines_are_not_phantom_deletions(tmp_path):
    """base_phantom_ratio counts PURE deletions only (sweep mutant M8).

    Live reworded l4-l6 in place; the checkout appended X. c1 is the nearest
    candidate and the true ancestor. A `replace` opcode is ours editing a
    line theirs left alone -- theirs retaining the OLD text proves nothing.
    Counting it rejects c1 (3 "deleted" lines, all retained) and the file
    falls to 'no base'. Measured on the real fixture: 6 replaced lines were
    legitimate edits."""
    co, roots, run = _two_way_repo(tmp_path)
    six = b"l1\nl2\nl3\nl4\nl5\nl6\n"
    (co / "dotclaude/F.md").write_bytes(six)
    run("add", "-A"); run("commit", "-qm", "c1 six lines")
    (co / "dotclaude/F.md").write_bytes(six + b"X\n")                # checkout appended
    run("add", "-A"); run("commit", "-qm", "c2 append X")
    live = b"l1\nl2\nl3\nE4\nE5\nE6\n"                                # live reworded
    (roots["CLAUDE_DIR"] / "F.md").write_bytes(live)
    found = merge.infer_base(co, "dotclaude/F.md", ours=live, theirs=six + b"X\n")
    assert found is not None, "replaced lines were counted as phantom deletions"
    assert merge._normalize_eol(found[0]) == six
    ratio, n = merge.base_phantom_ratio(six, live, six + b"X\n")
    assert (ratio, n) == (0.0, 0)


def test_status_attribution_normalises_a_crlf_live_file(tmp_path, capsys):
    """_classify compares through _normalize_eol on BOTH sides (mutant M9).

    A is checkout-ahead; live holds the seed content with CRLF endings, as a
    Windows editor leaves it. The attribution must still read 'checkout
    ahead'. Comparing the normalised base to the RAW live bytes says
    'both moved' -- a one-sided file reported as two-sided, the exact
    mislabel this file exists to prevent, back through a side door."""
    co, live, user = _live_ahead_and_checkout_ahead(tmp_path)
    (live / "A.md").write_bytes(b"a1\r\na2\r\n")                    # == seed, CRLF
    rc = main(["--checkout-dir", str(co), "--claude-dir", str(live),
               "--user-claude", str(user), "--no-color", "status", "--long"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "checkout ahead; live ==" in out
    assert "both moved" not in out


def test_cli_diff_path_crlf_vs_lf_reads_identical(env, capsys):
    """`ccs diff <path>` normalises line endings before comparing (mutant M12):
    a file that differs only in CRLF-vs-LF says 'identical' and exits 0, not
    a whole-file diff and exit 1."""
    claude, user, checkout, _, _ = env
    # Pin BOTH sides as bytes: the fixture's write_text emits CRLF on Windows,
    # which would make the two sides byte-equal and prove nothing.
    (checkout / "dotclaude" / "CLAUDE.md").write_bytes(b"# global memory v1\n")
    (claude / "CLAUDE.md").write_bytes(b"# global memory v1\r\n")
    rc = main(["--checkout-dir", str(checkout), "--claude-dir", str(claude),
               "--user-claude", str(user), "--no-color", "diff", "dotclaude/CLAUDE.md"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "identical" in out
