"""Merge orchestration tests.

Every test here maps to a failure that was MEASURED, not imagined. The
governing invariant, from DWP-5:

    Success means VALIDATED CONTENT SURVIVAL, never "the tool returned 0."

The three costumes that invariant wears, all reproduced below:
  * base == theirs -> git exits 0, one side's additions vanish
  * base == ours   -> git exits 0, the other side's additions vanish
  * a missing input -> git exits 255, which read as "255 conflicts" and looked
    like a correct rejection (a FALSE PASS found during development)
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dazzle_claude_config import merge
from dazzle_claude_config.manifest import Entry

# A miniature of the real CLAUDE.md divergence: each side added its own
# section to a shared document, and `theirs` also carries a path that was
# already fixed locally and must not come back.
BASE = "\n".join(["# Config", "", "shared rule A", "shared rule B", ""])
OURS = "\n".join(["# Config", "", "shared rule A", "shared rule B",
                  "user territory is ${CLAUDE_USER_DIR:-$HOME/claude}", ""])
THEIRS = "\n".join(["# Config", "", "shared rule A", "shared rule B",
                    "## Key-footer rule", "cite the whole filename",
                    "see /home/dev/claude/notes.md", ""])


def _entry(strategy="copy", target="CLAUDE.md", repo="dotclaude/CLAUDE.md"):
    return Entry(repo=repo, strategy=strategy, territory="dotclaude", target=target)


def _item(tmp_path: Path, base=BASE, ours=OURS, theirs=THEIRS, strategy="copy"):
    p = {}
    for name, text in (("base", base), ("ours", ours), ("theirs", theirs)):
        if text is None:
            p[name] = None
            continue
        f = tmp_path / f"{name}.md"
        # write_bytes, NOT write_text: on Windows write_text translates "\n"
        # to "\r\n", so a fixture built with .replace("\n", "\r\n") lands on
        # disk as "\r\r\n" -- a genuinely different file rather than the same
        # content in another line-ending style. That turned the EOL test into
        # a test of double-translation and it failed for the wrong reason.
        f.write_bytes(text.encode("utf-8"))
        p[name] = f
    return merge.MergeItem(entry=_entry(strategy), rel="",
                           live=p["ours"], repo=p["theirs"], base=p["base"])


PROBES = {"ours-side": "CLAUDE_USER_DIR", "theirs-side": "Key-footer rule"}


# --------------------------------------------------------------------------
# The invariant: a clean exit code is not evidence of a correct merge
# --------------------------------------------------------------------------

def test_correct_base_conflicts_and_is_not_accepted(tmp_path):
    """AC-2: a genuine two-way divergence must conflict, not merge silently."""
    item = _item(tmp_path)
    out = tmp_path / "merged"
    assert merge.seed(item, out) >= 1
    res = merge.validate(item, out, probes=PROBES)
    assert not res.ok
    assert any("conflict markers" in f for f in res.failures)


def test_base_equals_ours_exits_clean_but_gate_rejects(tmp_path):
    """AC-1, costume 2. git reports SUCCESS here; the gate must not.

    With base == ours, git concludes "ours changed nothing, take theirs" and
    exits 0. Our side's content is gone and nothing in git's output says so.
    """
    item = _item(tmp_path, base=OURS)
    out = tmp_path / "merged"
    conflicts = merge.seed(item, out)
    assert conflicts == 0, "precondition: git must consider this a clean merge"
    assert "CLAUDE_USER_DIR" not in out.read_text(encoding="utf-8")

    res = merge.validate(item, out, probes=PROBES)
    assert not res.ok, "a clean exit code must never be sufficient"
    assert res.survived["ours-side"] is False
    assert any("content lost" in f for f in res.failures)


def test_base_equals_theirs_exits_clean_but_gate_rejects(tmp_path):
    """AC-1, costume 1: the mirror image -- theirs' additions vanish."""
    item = _item(tmp_path, base=THEIRS)
    out = tmp_path / "merged"
    assert merge.seed(item, out) == 0
    res = merge.validate(item, out, probes=PROBES)
    assert not res.ok
    assert res.survived["theirs-side"] is False


def test_regressed_pattern_blocks_acceptance(tmp_path):
    """AC-4: upstream may legitimately carry a regression we already fixed."""
    item = _item(tmp_path, base=OURS)          # merges to theirs, dead path included
    out = tmp_path / "merged"
    merge.seed(item, out)
    res = merge.validate(item, out)
    assert any("/home/dev/claude/" in f for f in res.failures)


def test_result_identical_to_ours_is_rejected_without_probes(tmp_path):
    """REGRESSION, found on the first real CLI run.

    The tool was closed without saving, so `merged` stayed byte-identical to
    `ours` and theirs contributed nothing. ccs still printed "merged". The
    named-probe check would have caught it, but the CLI passed probes=None,
    so the most important check was inert in production while the unit tests
    -- which supply probes explicitly -- stayed green.

    The check therefore takes NO configuration, and asks what was LOST
    rather than what the result resembles -- a result equal to ours is a
    valid no-op merge when the other side had nothing unique to add.
    """
    item = _item(tmp_path)
    out = tmp_path / "merged"
    out.write_bytes(item.live.read_bytes())          # exactly ours
    res = merge.validate(item, out)                   # note: no probes
    assert not res.ok
    assert any("that the payload's copy has are missing" in f for f in res.failures)


def test_result_identical_to_theirs_is_rejected_without_probes(tmp_path):
    """The mirror: our local changes silently discarded."""
    item = _item(tmp_path)
    out = tmp_path / "merged"
    out.write_bytes(item.repo.read_bytes())          # exactly theirs
    res = merge.validate(item, out)
    assert not res.ok
    assert any("that your live file has are missing" in f for f in res.failures)


def test_identical_sides_do_not_trigger_the_identity_check(tmp_path):
    """When ours == theirs there is nothing to merge, so equality is fine."""
    item = _item(tmp_path, theirs=OURS)
    out = tmp_path / "merged"
    out.write_bytes(item.live.read_bytes())
    res = merge.validate(item, out)
    assert not any("identical to" in f for f in res.failures)


def test_clean_result_is_accepted(tmp_path):
    """The gate must not reject everything -- a real union passes."""
    item = _item(tmp_path)
    out = tmp_path / "merged"
    out.write_text(OURS + "## Key-footer rule\ncite the whole filename\n",
                   encoding="utf-8")
    res = merge.validate(item, out, probes=PROBES)
    assert res.ok, res.failures
    assert res.survived == {"ours-side": True, "theirs-side": True}


def test_invented_content_is_rejected(tmp_path):
    """AC-9: an AI proposal may not introduce text from neither side."""
    item = _item(tmp_path)
    out = tmp_path / "merged"
    out.write_text(OURS + "a line nobody wrote\n", encoding="utf-8")
    res = merge.validate(item, out)
    assert any("neither side" in f for f in res.failures)


# --------------------------------------------------------------------------
# Regressions for the two bugs found while building this
# --------------------------------------------------------------------------

def test_diff3_base_marker_is_a_conflict_marker_not_invented_content(tmp_path):
    """`|||||||` is the --diff3 base separator.

    Omitting it failed twice at once: the marker went undetected AND its line
    was reported as invented content, since it appears in none of the inputs.
    """
    assert "|||||||" in merge.CONFLICT_MARKERS
    item = _item(tmp_path)
    out = tmp_path / "merged"
    merge.seed(item, out)
    assert "|||||||" in out.read_text(encoding="utf-8")
    res = merge.validate(item, out)
    assert not any("neither side" in f for f in res.failures), \
        "diff3 markers must not be mistaken for invented content"


def test_missing_input_raises_instead_of_reporting_255_conflicts(tmp_path):
    """A missing input made git exit 255 -> read as '255 conflicts', empty
    output, and a rejection for entirely the wrong reason: a FALSE PASS."""
    item = _item(tmp_path)
    item.base = tmp_path / "does-not-exist.md"
    with pytest.raises(merge.MergeError, match="base input not found"):
        merge.seed(item, tmp_path / "merged")


# --------------------------------------------------------------------------
# Strategy awareness (Gap 2 / AC-22) and territory coverage (Gap 1 / AC-20)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("strategy", sorted(merge.MERGE_REFUSED_STRATEGIES))
def test_composed_targets_are_refused_with_the_source_layer_named(strategy):
    """AC-22/23: merging a rendered target writes machine-specific values into
    the file everyone shares -- and can push them to a public repo via a PR."""
    entry = Entry(repo="settings/settings.base.json", strategy=strategy,
                  territory="dotclaude", target="settings.json",
                  overlays=["machines/{host}/settings.overlay.json"])
    d = type("D", (), {"entry": entry, "modified": [""],
                       "live_base": Path("live"), "repo_base": Path("repo")})()
    items = merge._items_for_diff(d)
    assert len(items) == 1 and not items[0].mergeable
    assert "composes its target" in items[0].reason
    assert "machines/{host}/settings.overlay.json" in items[0].reason


def test_unknown_strategy_is_refused_not_assumed_copyable():
    entry = Entry(repo="x", strategy="brand-new", territory="dotclaude", target="x")
    d = type("D", (), {"entry": entry, "modified": [""],
                       "live_base": Path("live"), "repo_base": Path("repo")})()
    assert not merge._items_for_diff(d)[0].mergeable


def test_userclaude_is_a_first_class_territory(tmp_path):
    """AC-20: userclaude -> ~/claude must merge like dotclaude -> ~/.claude.

    Half the payload silently never merging is the invariant failing again.
    """
    entry = Entry(repo="userclaude/scripts/x.js", strategy="copy",
                  territory="userclaude", target="scripts/x.js")
    d = type("D", (), {"entry": entry, "modified": [""],
                       "live_base": tmp_path / "live", "repo_base": tmp_path / "repo"})()
    items = merge._items_for_diff(d)
    assert len(items) == 1 and items[0].mergeable
    assert items[0].label == "scripts/x.js"


# --------------------------------------------------------------------------
# Tool resolution (AC-5) and non-TTY refusal (AC-7)
# --------------------------------------------------------------------------

def test_tool_with_missing_binary_is_unusable(tmp_path, monkeypatch):
    """Measured: `merge.tool = bc` -> bare `BCompare.exe`, not on PATH.

    Trusting the configured name fails on the machine that configured it.
    """
    def fake_git(args, cwd=None):
        if args[:2] == ["config", "--get"]:
            return 0, "definitely-not-a-real-binary-xyz $LOCAL $REMOTE"
        return 1, ""
    monkeypatch.setattr(merge, "_git", fake_git)
    assert merge._tool_usable("phantom") is False
    with pytest.raises(merge.MergeError, match="binary was not found"):
        merge.resolve_tool("phantom")


def test_placeholders_are_substituted_not_left_to_the_shell(tmp_path):
    """REGRESSION. Exporting $LOCAL/$BASE/$REMOTE/$MERGED as env vars is not
    enough on Windows: shell=True runs through cmd.exe, which expands %VAR%
    and leaves $VAR alone. Beyond Compare opened four panes literally named
    "$REMOTE", "$BASE", "$LOCAL", "$MERGED" and reported File Not Found.
    """
    item = _item(tmp_path)
    merged, base = tmp_path / "m.txt", tmp_path / "b.txt"
    cmd = '"bcomp.exe" --wait "$REMOTE" "$LOCAL" "$BASE" "$MERGED"'
    line = merge.substitute(cmd, item, merged, base)
    for placeholder in ("$REMOTE", "$LOCAL", "$BASE", "$MERGED"):
        assert placeholder not in line, f"{placeholder} survived substitution"
    assert str(item.live) in line and str(item.repo) in line
    assert str(merged) in line and str(base) in line


def test_substitute_handles_braced_form_and_longest_name_first(tmp_path):
    """${MERGED} must not be mangled by a shorter name matching first."""
    item = _item(tmp_path)
    merged, base = tmp_path / "m.txt", tmp_path / "b.txt"
    line = merge.substitute("x ${MERGED} ${BASE} $LOCAL", item, merged, base)
    assert "${" not in line and "$" not in line
    assert str(merged) in line


def test_executable_of_handles_quoted_paths_with_spaces():
    cmd = '"C:\\app\\diff\\Beyond Compare 4\\bcomp.exe" --wait "$LOCAL" "$REMOTE"'
    assert merge._executable_of(cmd).endswith("bcomp.exe")
    assert "Beyond Compare 4" in merge._executable_of(cmd)


def test_non_interactive_refuses_to_launch(tmp_path, monkeypatch):
    """AC-7: a GUI on a CI runner hangs forever; picking a side silently is
    exactly what this module exists to prevent."""
    monkeypatch.setattr(merge, "interactive", lambda: False)
    item = _item(tmp_path)
    with pytest.raises(merge.MergeError, match="no console attached"):
        merge.launch("anything", item, tmp_path / "m", tmp_path / "base.md")


def test_ci_env_blocks_launch_even_with_a_console(monkeypatch):
    monkeypatch.setattr(merge, "_console_attached", lambda s: True)
    monkeypatch.setenv("CI", "true")
    assert merge.interactive() is False


def test_ccs_no_launch_override(monkeypatch):
    monkeypatch.setattr(merge, "_console_attached", lambda s: True)
    monkeypatch.setenv("CCS_NO_LAUNCH", "1")
    assert merge.interactive() is False


def test_isatty_alone_is_not_trusted_on_windows(monkeypatch):
    """REGRESSION. On Windows, /dev/null maps to NUL -- a character device --
    so isatty() returns True under `> /dev/null` and the guard let a GUI open.

    Measured:  >/dev/null -> isatty()=True, GetConsoleMode()=False

    Three BeyondCompare windows launched from a redirected command and hung it.
    The guard must consult the console API, never isatty() alone.
    """
    class FakeNul:
        def isatty(self):      # what NUL claims on Windows
            return True
        def fileno(self):
            raise OSError("no real fd")
    monkeypatch.setattr(merge, "_console_attached", lambda s: False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("CCS_NO_LAUNCH", raising=False)
    assert merge.interactive() is False, \
        "a stream claiming isatty()=True must still be refused when it is not a console"


# --------------------------------------------------------------------------
# No-base degradation (Phase 1 has no ancestry yet)
# --------------------------------------------------------------------------

def test_no_base_is_an_honest_two_way_not_an_invented_third_input(tmp_path):
    """Inventing a base is the failure that started this whole design."""
    item = _item(tmp_path, base=None)
    out = tmp_path / "merged"
    assert merge.seed(item, out) == 1          # differs => unresolved
    assert out.read_text(encoding="utf-8") == OURS
    assert item.base is None


def test_no_base_identical_sides_is_not_a_conflict(tmp_path):
    item = _item(tmp_path, base=None, theirs=OURS)
    assert merge.seed(item, tmp_path / "merged") == 0


def test_eol_only_difference_is_not_a_conflict(tmp_path):
    """AC-15: CRLF vs LF alone must never manufacture a merge."""
    item = _item(tmp_path, base=None, theirs=OURS.replace("\n", "\r\n"))
    assert merge.seed(item, tmp_path / "merged") == 0


def test_write_back_installs_on_both_sides(tmp_path):
    item = _item(tmp_path)
    out = tmp_path / "merged"
    out.write_text("final\n", encoding="utf-8")
    merge._write_back(item, out)
    assert item.live.read_text(encoding="utf-8") == "final\n"
    assert item.repo.read_text(encoding="utf-8") == "final\n"


def test_git_merge_file_is_available():
    """The engine is git's. If this fails, nothing else here is meaningful."""
    p = subprocess.run(["git", "merge-file", "-h"], capture_output=True)
    assert b"merge-file" in p.stdout + p.stderr


def test_crlf_vs_lf_inputs_do_not_collapse_into_one_giant_conflict(tmp_path):
    """REGRESSION, the fifth line-ending bug of this effort and the worst.

    Live files are CRLF on Windows; git hands back LF. Without normalising,
    EVERY line differs and the merge becomes one ~1000-line conflict.

    Measured on the real CLAUDE.md fixture:
        raw        1983 lines, ~1000 inside a single conflict  (unusable)
        normalised 1019 lines,     50 inside one conflict (4%) (reviewable)
    """
    base = "\n".join(f"line {i}" for i in range(40)) + "\n"
    ours = base.replace("line 5", "line 5 OURS")
    theirs = base.replace("line 30", "line 30 THEIRS")
    item = _item(tmp_path, base=base, ours=ours.replace("\n", "\r\n"), theirs=theirs)
    out = tmp_path / "merged"
    conflicts = merge.seed(item, out)
    text = out.read_text(encoding="utf-8")
    assert conflicts == 0, "edits in different regions must auto-merge"
    assert "line 5 OURS" in text and "line 30 THEIRS" in text
    assert "<<<<<<<" not in text


def test_write_back_preserves_the_live_files_line_endings(tmp_path):
    """The merge runs on LF copies; rewriting a CRLF file as LF would show up
    as a whole-file change in every later diff."""
    item = _item(tmp_path, ours=OURS.replace("\n", "\r\n"))
    merged = tmp_path / "merged"
    merged.write_bytes(b"alpha\nbeta\n")
    merge._write_back(item, merged)
    assert item.live.read_bytes() == b"alpha\r\nbeta\r\n"


def test_union_keeps_both_sides_without_markers(tmp_path):
    """--union is the right answer when each side ADDED different things.

    Real case: the inferred base already contained a section this machine
    never had, so git read "we never had it" as "we deleted it" and raised a
    delete/modify conflict. Nothing genuinely conflicted; a union is correct.
    """
    item = _item(tmp_path)
    out = tmp_path / "merged"
    conflicts = merge.seed(item, out, union=True)
    text = out.read_text(encoding="utf-8")
    assert conflicts == 0 and "<<<<<<<" not in text
    assert "CLAUDE_USER_DIR" in text        # ours survived
    assert "Key-footer rule" in text        # theirs survived


def test_union_duplication_is_caught_by_validation(tmp_path):
    """Union's characteristic failure: the same paragraph landing twice."""
    item = _item(tmp_path)
    out = tmp_path / "merged"
    line = "shared rule A that is quite long and definitely over forty characters"
    for p in (item.live, item.repo, item.base):
        p.write_bytes((line + "\n").encode())
    out.write_bytes((line + "\n" + line + "\n").encode())
    res = merge.validate(item, out)
    assert any("duplicated" in f for f in res.failures)


def test_union_is_not_the_default(tmp_path):
    """Union silently keeps both sides, so it must always be opt-in."""
    item = _item(tmp_path)
    out = tmp_path / "merged"
    assert merge.seed(item, out) >= 1        # markers, not a silent union
    assert "<<<<<<<" in out.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Status reporting: "N files differ" answers the wrong question
# --------------------------------------------------------------------------

def test_line_stats_separates_each_side_from_true_conflicts(tmp_path):
    """`1 differs on both sides` counts FILES and reads as differences.

    Measured on the real CLAUDE.md pair: 10 regions -- 2 lines only in live,
    50 only in the checkout, 6 genuinely changed on both.
    """
    from dazzle_claude_config.syncmap import line_stats
    a = tmp_path / "live"; b = tmp_path / "repo"
    # Unchanged anchor lines between each edit so difflib cannot coalesce a
    # delete + insert into a single `replace`. The first version of this test
    # asserted GUESSED opcode grouping and failed for that reason rather than
    # because of the code; the measured grouping is delete / replace / insert.
    a.write_bytes(b"anchor1\nonly-live\nanchor2\nboth-A\nanchor3\n")
    b.write_bytes(b"anchor1\nanchor2\nboth-B\nanchor3\nonly-repo\n")
    only_live, changed, only_repo, regions = line_stats(a, b)
    assert (only_live, changed, only_repo, regions) == (1, 1, 1, 3)


def test_line_stats_ignores_eol_only_differences(tmp_path):
    """The sixth EOL trap: a CRLF live file must not report every line changed."""
    from dazzle_claude_config.syncmap import line_stats
    a = tmp_path / "live"; b = tmp_path / "repo"
    body = "alpha\nbeta\ngamma\n"
    a.write_bytes(body.replace("\n", "\r\n").encode())
    b.write_bytes(body.encode())
    assert line_stats(a, b) == (0, 0, 0, 0)


# --------------------------------------------------------------------------
# The guard that should have existed before 50 lines went missing
# --------------------------------------------------------------------------

def _two_way_repo(tmp_path):
    """A checkout whose HEAD and live tree each hold unique content.

    Uses conftest's GIT_ID, which carries `commit.gpgsign=false`. Signing is
    ON globally on this machine, so a fixture that shells out to `git commit`
    without it stalls waiting on a passphrase -- which is exactly what made
    this file time out and fail intermittently before.
    """
    import subprocess as sp
    from conftest import GIT_ID
    co = tmp_path / "checkout"; (co / "dotclaude").mkdir(parents=True)
    live = tmp_path / "live"; live.mkdir()

    def run(*a):
        r = sp.run(["git", *GIT_ID, "-C", str(co), *a], capture_output=True, text=True)
        assert r.returncode == 0, f"git {a}: {r.stderr}"
        return r

    sp.run(["git", "init", "-q", str(co)], capture_output=True)
    (co / "ccs-manifest.json").write_text(
        '{"manifest_version":1,'
        '"territories":{"dotclaude":{"root_var":"CLAUDE_DIR","repo_dir":"dotclaude"}},'
        '"entries":[{"repo":"dotclaude/F.md","territory":"dotclaude",'
        '"target":"F.md","strategy":"copy"}]}', encoding="utf-8")
    base = "shared\ncommon\n"
    (co / "dotclaude/F.md").write_bytes(base.encode())
    run("add", "-A"); run("commit", "-qm", "base")
    (co / "dotclaude/F.md").write_bytes((base + "THEIRS\n").encode())
    run("add", "-A"); run("commit", "-qm", "theirs adds")
    (live / "F.md").write_bytes((base + "OURS\n").encode())
    return co, {"CLAUDE_DIR": live, "USER_CLAUDE": tmp_path / "uc"}, run


def test_two_way_labels_flags_a_file_both_sides_changed(tmp_path):
    """collect/apply bucket `modified` in with the safe one-sided cases and
    copy straight over it. For a genuinely two-way file that is silent data
    loss reported as success -- measured at 50 lines of CLAUDE.md."""
    from dazzle_claude_config.manifest import Manifest
    co, roots, _run = _two_way_repo(tmp_path)
    assert merge.two_way_labels(Manifest.load(co), co, roots) == ["F.md"]


def test_two_way_labels_ignores_one_sided_drift(tmp_path):
    """Only BOTH-sides files are refused; one-way copies stay usable."""
    from dazzle_claude_config.manifest import Manifest
    co, roots, _run = _two_way_repo(tmp_path)
    # make live match HEAD -> no divergence at all
    (roots["CLAUDE_DIR"] / "F.md").write_bytes(
        (co / "dotclaude/F.md").read_bytes())
    assert merge.two_way_labels(Manifest.load(co), co, roots) == []


def test_two_way_labels_reports_when_no_base_can_be_found(tmp_path):
    """Unknown is not the same as safe: a file whose base cannot be recovered
    is still reported, because nothing proves the copy is lossless."""
    from dazzle_claude_config.manifest import Manifest
    co, roots, run = _two_way_repo(tmp_path)
    # squash history so only HEAD exists -> infer_base has nothing to offer
    run("checkout", "-q", "--orphan", "flat")
    run("add", "-A"); run("commit", "-qm", "flat")
    assert merge.two_way_labels(Manifest.load(co), co, roots) == ["F.md"]


def _two_way_dir_repo(tmp_path):
    """Same divergence shape as _two_way_repo, but inside a DIRECTORY entry.

    This is run-03's minimal fixture: it differs from the three tests above by
    exactly one property -- the manifest entry targets `skills/` rather than a
    single file -- and that one property was enough for the guard to skip the
    file entirely while a real collect destroyed its diverged edit (TW1).
    """
    import subprocess as sp
    from conftest import GIT_ID
    co = tmp_path / "checkout"; (co / "dotclaude" / "skills").mkdir(parents=True)
    live = tmp_path / "live"; (live / "skills").mkdir(parents=True)

    def run(*a):
        r = sp.run(["git", *GIT_ID, "-C", str(co), *a], capture_output=True, text=True)
        assert r.returncode == 0, f"git {a}: {r.stderr}"
        return r

    sp.run(["git", "init", "-q", str(co)], capture_output=True)
    (co / "ccs-manifest.json").write_text(
        '{"manifest_version":1,'
        '"territories":{"dotclaude":{"root_var":"CLAUDE_DIR","repo_dir":"dotclaude"}},'
        '"entries":[{"repo":"dotclaude/skills","territory":"dotclaude",'
        '"target":"skills","strategy":"copy"}]}', encoding="utf-8")
    base = "shared\ncommon\n"
    (co / "dotclaude/skills/alpha.md").write_bytes(base.encode())
    run("add", "-A"); run("commit", "-qm", "base")
    (co / "dotclaude/skills/alpha.md").write_bytes((base + "THEIRS\n").encode())
    run("add", "-A"); run("commit", "-qm", "theirs adds")
    (live / "skills/alpha.md").write_bytes((base + "OURS\n").encode())
    return co, {"CLAUDE_DIR": live, "USER_CLAUDE": tmp_path / "uc"}, run


def test_two_way_labels_covers_directory_entries(tmp_path):
    """TW1 regression (checklist run-03, Finding 1, HIGH).

    The guard iterated entries and tested is_file() on the entry TARGET, so a
    directory entry was skipped wholesale and a real `ccs collect` silently
    overwrote a committed, diverged payload edit while exiting 0. The label is
    per-file so the refusal names what is actually at risk.
    """
    from dazzle_claude_config.manifest import Manifest
    co, roots, _run = _two_way_dir_repo(tmp_path)
    assert merge.two_way_labels(Manifest.load(co), co, roots) == ["skills/alpha.md"]


def test_two_way_labels_dir_entry_resolved_worktree_not_labeled(tmp_path):
    """A finished merge (live == checkout WORKING TREE) must not be re-flagged
    -- for directory members exactly as for single files. diff_all's modified
    list is EOL-normalized, so the resolved file never reaches the guard."""
    from dazzle_claude_config.manifest import Manifest
    co, roots, _run = _two_way_dir_repo(tmp_path)
    (roots["CLAUDE_DIR"] / "skills/alpha.md").write_bytes(
        (co / "dotclaude/skills/alpha.md").read_bytes())
    assert merge.two_way_labels(Manifest.load(co), co, roots) == []


# HANDSHAKE with the companion S1 commit: that commit deletes the @S1_INTERLOCK
# decorator line below -- a one-line edit -- in the SAME commit that fixes
# infer_base's equality skip. strict=True means the suite goes red on the
# unexpected pass, so the marker cannot be forgotten between the two commits.
# See dwp8 + the s1-phantom-asymmetry note (both in private/claude).
# (S1 landed 2026-08-21 -- the strict xfail fired as designed and the marker
# was removed in the same commit. The test below is now a plain regression.)
def test_two_way_labels_dir_entry_one_sided_live_unchanged(tmp_path):
    """Live equals the BASE commit verbatim -- only the checkout moved. That is
    one-sided drift and must not be refused; a plain apply is the right call
    (measured on a real machine as 22 files sent to 22 empty merges)."""
    from dazzle_claude_config.manifest import Manifest
    co, roots, _run = _two_way_dir_repo(tmp_path)
    (roots["CLAUDE_DIR"] / "skills/alpha.md").write_bytes(b"shared\ncommon\n")
    assert merge.two_way_labels(Manifest.load(co), co, roots) == []


def test_two_way_labels_ignores_diverged_seed_entries(tmp_path):
    """Seed-if-absent entries cannot be damaged by either one-way verb --
    collect never touches them and apply never overwrites an existing live
    file -- so refusing the WHOLE run over a diverged seed was pure
    over-refusal. Observed live: the public payload's seed CLAUDE.md refusal
    masked every other report in the run."""
    import subprocess as sp
    from conftest import GIT_ID
    from dazzle_claude_config.manifest import Manifest
    co = tmp_path / "checkout"; (co / "dotclaude").mkdir(parents=True)
    live = tmp_path / "live"; live.mkdir()

    def run(*a):
        r = sp.run(["git", *GIT_ID, "-C", str(co), *a], capture_output=True, text=True)
        assert r.returncode == 0, f"git {a}: {r.stderr}"

    sp.run(["git", "init", "-q", str(co)], capture_output=True)
    (co / "ccs-manifest.json").write_text(
        '{"manifest_version":1,'
        '"territories":{"dotclaude":{"root_var":"CLAUDE_DIR","repo_dir":"dotclaude"}},'
        '"entries":[{"repo":"dotclaude/SEED.md","territory":"dotclaude",'
        '"target":"SEED.md","strategy":"seed-if-absent"}]}', encoding="utf-8")
    (co / "dotclaude/SEED.md").write_bytes(b"shared\n")
    run("add", "-A"); run("commit", "-qm", "base")
    (co / "dotclaude/SEED.md").write_bytes(b"shared\nTHEIRS\n")
    run("add", "-A"); run("commit", "-qm", "theirs")
    (live / "SEED.md").write_bytes(b"shared\nOURS\n")
    assert merge.two_way_labels(Manifest.load(co), co, roots={
        "CLAUDE_DIR": live, "USER_CLAUDE": tmp_path / "uc"}) == []


# --------------------------------------------------------------------------
# Mutation-sweep killers (tests/mutation/tw1-report.json). Each is named for
# the survivor it kills; a suite that cannot go red under these mutants was
# not constraining the code, only executing it.
# --------------------------------------------------------------------------

def test_partial_entries_are_skipped_not_crashed(tmp_path):
    """Kills m1 (or->and on the partial-entry skip). An entry missing its
    territory OR its target is ignored; under the mutant it proceeds and
    KeyErrors on territories[None]."""
    from dazzle_claude_config.manifest import Manifest
    co, roots, _run = _two_way_repo(tmp_path)
    m = Manifest.load(co)
    # non-copy strategy keeps them out of diff_all's pass, scoping this test
    # to the entry-loop guard the mutant breaks
    m.entries.append(Entry(repo="dotclaude/orphan.md", strategy="seed-if-absent",
                           territory=None, target="orphan.md"))
    m.entries.append(Entry(repo="dotclaude/orphan2.md", strategy="seed-if-absent",
                           territory="dotclaude", target=None))
    got = list(merge._head_candidates(m, co, roots))
    assert [rel for _, rel, _ in got] == [""], \
        "only the real single-file entry may surface; partial entries are skipped"


def test_head_axis_ignores_eol_only_difference(tmp_path):
    """Kills m5 (dropped normalization in the HEAD equality skip). A live file
    that differs from the committed blob ONLY in line endings -- the normal
    state of a Windows live tree against LF git blobs -- is not a merge
    candidate. Six separate EOL failures in this project's history and, until
    this test, not one CRLF fixture in the suite."""
    from dazzle_claude_config.manifest import Manifest
    co, roots, _run = _two_way_repo(tmp_path)
    # live content == HEAD content, but CRLF
    head = (co / "dotclaude/F.md").read_bytes()
    (roots["CLAUDE_DIR"] / "F.md").write_bytes(head.replace(b"\n", b"\r\n"))
    items = merge._head_items(Manifest.load(co), co, roots, [],
                              tmp_path / "stage", "auto")
    assert items == []


def test_two_way_guard_ignores_eol_only_difference(tmp_path):
    """Kills m10 (dropped normalization on the guard's live side).

    The naive fixture (live == HEAD modulo EOL, worktree untouched) cannot
    kill this mutant: files_differ is normalized, so the file never enters
    d.modified and the guard never runs -- an invariant guard, not a killer
    (round-2 sweep proved it). To exercise the mutated line the file must
    genuinely differ from the WORKTREE (so diff_all surfaces it) while
    matching HEAD modulo EOL (so the ours==theirs skip is what decides).
    Real code: content-equal to HEAD, no label. Mutant: raw CRLF bytes
    differ from the LF blob, the skip fails, and a phantom two-way appears.
    """
    from dazzle_claude_config.manifest import Manifest
    co, roots, _run = _two_way_repo(tmp_path)
    head = (co / "dotclaude/F.md").read_bytes()
    # live == HEAD content, CRLF endings
    (roots["CLAUDE_DIR"] / "F.md").write_bytes(head.replace(b"\n", b"\r\n"))
    # worktree edited (uncommitted) so diff_all lists the file as modified
    (co / "dotclaude/F.md").write_bytes(head + b"uncommitted worktree edit\n")
    assert merge.two_way_labels(Manifest.load(co), co, roots) == []


def test_phantom_rejection_measures_base_to_ours_deletions(tmp_path):
    """Kills m6 (swapped ours/theirs into infer_base). base_phantom_ratio is
    DIRECTIONAL by design -- it counts base->OURS deletions retained by
    theirs. Ours deletes three base lines that theirs retains: the candidate
    must be REJECTED as a base (sibling recorded), because accepting it would
    re-delete content theirs still holds. Swapping the arguments measures the
    empty direction and accepts it. This directionality is a load-bearing
    contract for the S1 fix (see the s1-phantom-asymmetry note)."""
    import subprocess as sp
    from conftest import GIT_ID
    from dazzle_claude_config.manifest import Manifest
    co = tmp_path / "checkout"; (co / "dotclaude").mkdir(parents=True)
    live = tmp_path / "live"; live.mkdir()

    def run(*a):
        r = sp.run(["git", *GIT_ID, "-C", str(co), *a], capture_output=True, text=True)
        assert r.returncode == 0, f"git {a}: {r.stderr}"

    sp.run(["git", "init", "-q", str(co)], capture_output=True)
    (co / "ccs-manifest.json").write_text(
        '{"manifest_version":1,'
        '"territories":{"dotclaude":{"root_var":"CLAUDE_DIR","repo_dir":"dotclaude"}},'
        '"entries":[{"repo":"dotclaude/F.md","territory":"dotclaude",'
        '"target":"F.md","strategy":"copy"}]}', encoding="utf-8")
    # EXACTLY two commits: the walk skips HEAD, leaving the fat base as the
    # single candidate -- infer_base continues past rejections, so any extra
    # history would hand it an acceptable older base and mask the verdict.
    base = b"keep\ndel one\ndel two\ndel three\ncommon\n"
    (co / "dotclaude/F.md").write_bytes(base)
    run("add", "-A"); run("commit", "-qm", "fat base")
    (co / "dotclaude/F.md").write_bytes(base + b"THEIRS-TAIL\n")   # theirs retains all
    run("add", "-A"); run("commit", "-qm", "theirs adds")
    roots = {"CLAUDE_DIR": live, "USER_CLAUDE": tmp_path / "uc"}
    (live / "F.md").write_bytes(b"keep\ncommon\nOURS-TAIL\n")      # ours deleted 3
    items = merge._head_items(Manifest.load(co), co, roots, [],
                              tmp_path / "stage", "auto")
    assert len(items) == 1
    assert items[0].base is None, \
        "a candidate whose deletions theirs retains is a sibling, not a base"
    assert items[0].sibling is not None


def test_head_axis_refuses_render_strategy_items(tmp_path):
    """Kills m7 (inverted refusal). A render entry diverging at HEAD gets an
    item with a refusal reason -- merging the RENDERED target would bake this
    machine's overlay values into the shared base (AC-22/23)."""
    import subprocess as sp
    from conftest import GIT_ID
    from dazzle_claude_config.manifest import Manifest
    co = tmp_path / "checkout"; (co / "settings").mkdir(parents=True)
    live = tmp_path / "live"; live.mkdir()

    def run(*a):
        r = sp.run(["git", *GIT_ID, "-C", str(co), *a], capture_output=True, text=True)
        assert r.returncode == 0, f"git {a}: {r.stderr}"

    sp.run(["git", "init", "-q", str(co)], capture_output=True)
    (co / "ccs-manifest.json").write_text(
        '{"manifest_version":1,'
        '"territories":{"dotclaude":{"root_var":"CLAUDE_DIR","repo_dir":"dotclaude"}},'
        '"entries":[{"repo":"settings/settings.base.json","territory":"dotclaude",'
        '"target":"settings.json","strategy":"render"}]}', encoding="utf-8")
    (co / "settings/settings.base.json").write_bytes(b'{"a": 1}\n')
    run("add", "-A"); run("commit", "-qm", "base")
    (live / "settings.json").write_bytes(b'{"a": 2}\n')
    items = merge._head_items(Manifest.load(co), co, roots={
        "CLAUDE_DIR": live, "USER_CLAUDE": tmp_path / "uc"}, already=[],
        stage=tmp_path / "stage", base_mode="auto")
    assert len(items) == 1
    assert not items[0].mergeable
    assert "composes its target" in (items[0].reason or "")


def test_head_items_infer_a_base_for_directory_entry_files(tmp_path):
    """The second face of TW1 (run-04, four FAIL-UNEXPECTED, one cause):
    _head_items had the same is_file() gate, so base inference NEVER ran for
    directory-entry files and their merges fell through to the baseless
    worktree axis. With a true ancestor in history, the HEAD axis must now
    produce the item, per-file, with the base recovered."""
    from dazzle_claude_config.manifest import Manifest
    co, roots, _run = _two_way_dir_repo(tmp_path)
    stage = tmp_path / "stage"
    items = merge._head_items(Manifest.load(co), co, roots, [], stage, "auto")
    assert [i.label for i in items] == ["skills/alpha.md"]
    it = items[0]
    assert it.rel == "alpha.md"
    assert it.base is not None, "true ancestor exists; HEAD axis must find it"
    assert it.base.read_bytes().replace(b"\r\n", b"\n") == b"shared\ncommon\n"
    assert it.repo_dest == co / "dotclaude/skills/alpha.md"


# --------------------------------------------------------------------------
# Install destination: found by verifying a real run, not by the suite
# --------------------------------------------------------------------------

def test_write_back_installs_into_the_checkout_not_the_staging_copy(tmp_path):
    """REGRESSION for the 0.3.0 bug.

    On the HEAD axis, `repo` is the STAGED copy of theirs inside the merge
    workspace; the checkout's real file is a different path entirely. The old
    write-back wrote to `repo`, so `--accept` put the merge in the live tree,
    clobbered the staged copy of theirs, and left the payload repo untouched
    -- while reporting success.

    The previous test could not catch this: its fixture made `live` and `repo`
    two ordinary temp files, where writing to the wrong one is indistinguish-
    able from writing to the right one. This asserts the DESTINATION.
    """
    staging = tmp_path / "workspace" / "F.md.head"
    staging.parent.mkdir(parents=True)
    staging.write_bytes(b"theirs-content\n")
    checkout = tmp_path / "checkout" / "dotclaude" / "F.md"
    checkout.parent.mkdir(parents=True)
    checkout.write_bytes(b"checkout-original\n")
    live = tmp_path / "live" / "F.md"
    live.parent.mkdir(parents=True)
    live.write_bytes(b"ours-content\n")

    item = merge.MergeItem(entry=_entry(), rel="", live=live,
                           repo=staging, repo_dest=checkout)
    merged = tmp_path / "merged"
    merged.write_bytes(b"MERGED\n")
    merge._write_back(item, merged)

    assert checkout.read_bytes() == b"MERGED\n", "the checkout must receive it"
    assert live.read_bytes() == b"MERGED\n", "so must the live tree"
    assert staging.read_bytes() == b"theirs-content\n", \
        "the staged copy of theirs must be left alone"


def test_backup_is_written_before_either_side_is_touched(tmp_path):
    """--accept printed 'originals backed up' while creating no directory.

    Same root cause: it backed up `repo`, the staging path. Both originals
    must be recoverable, and the backup must precede the writes.
    """
    staging = tmp_path / "ws" / "F.md.head"; staging.parent.mkdir(parents=True)
    staging.write_bytes(b"theirs\n")
    checkout = tmp_path / "co" / "F.md"; checkout.parent.mkdir(parents=True)
    checkout.write_bytes(b"CHECKOUT-ORIGINAL\n")
    live = tmp_path / "lv" / "F.md"; live.parent.mkdir(parents=True)
    live.write_bytes(b"LIVE-ORIGINAL\n")
    bdir = tmp_path / "backups"

    item = merge.MergeItem(entry=_entry(), rel="", live=live,
                           repo=staging, repo_dest=checkout)
    merged = tmp_path / "m"; merged.write_bytes(b"MERGED\n")
    merge._write_back(item, merged, bdir)

    assert bdir.is_dir(), "the backup directory must actually be created"
    saved = {p.name: p.read_bytes() for p in bdir.iterdir()}
    assert b"LIVE-ORIGINAL\n" in saved.values(), "live original must be saved"
    assert b"CHECKOUT-ORIGINAL\n" in saved.values(), \
        "the CHECKOUT original must be saved -- not the staging copy"


def test_reseed_only_when_the_output_pane_is_untouched(tmp_path):
    """REGRESSION: the resume guard compared against OURS, not the seed.

    Wrong in both directions. A union seed never equals ours, so every re-run
    reported "resumed" even when nothing had been edited; and whenever an
    edited result happened to coincide with ours, it was mistaken for a fresh
    seed and overwritten -- destroying real work done in the diff tool.
    """
    merged = tmp_path / "m"
    stamp = tmp_path / "m.seed"
    merged.write_bytes(b"SEEDED\n")
    stamp.write_bytes(b"SEEDED\n")
    # untouched -> safe to re-seed
    assert not merge._differs_bytes(merged, stamp)
    # human edits -> must be preserved
    merged.write_bytes(b"SEEDED\nplus my edit\n")
    assert merge._differs_bytes(merged, stamp)


def test_edited_result_equal_to_ours_is_still_treated_as_edited(tmp_path):
    """The precise case the old guard destroyed: a user resolves entirely in
    favour of their own side, so the output equals `ours` -- and the old test
    (`merged != live`) read that as an untouched seed and re-seeded over it."""
    item = _item(tmp_path)
    merged = tmp_path / "m"
    stamp = tmp_path / "m.seed"
    stamp.write_bytes(b"UNION-SEED\n")           # what we wrote
    merged.write_bytes(item.live.read_bytes())    # what the user chose: ours
    assert merge._differs_bytes(merged, stamp), \
        "differs from the seed, so it is an edit and must be kept"


def test_difftool_registry_is_separate_from_mergetool(monkeypatch):
    """git keeps difftool.<n>.cmd and mergetool.<n>.cmd apart, and a name may
    exist in one and not the other. Measured here: `bc4` is difftool-only and
    `beyondcompare4` mergetool-only -- so reusing the merge resolver for a
    two-pane diff would miss a working tool sitting right there."""
    calls = []

    def fake_git(args, cwd=None):
        calls.append(args)
        if args[:2] == ["config", "--get"] and args[2].startswith("difftool."):
            return 0, "definitely-not-real.exe $LOCAL $REMOTE"
        return 1, ""
    monkeypatch.setattr(merge, "_git", fake_git)
    with pytest.raises(merge.MergeError, match="binary was not found"):
        merge.resolve_difftool("phantom")
    assert any(a[2].startswith("difftool.") for a in calls if len(a) > 2), \
        "must consult the DIFFtool registry, not mergetool"


def test_difftool_refuses_without_a_console(tmp_path, monkeypatch):
    """Same AC-7 rule as merge: never open a GUI where nobody can close it."""
    monkeypatch.setattr(merge, "interactive", lambda: False)
    a = tmp_path / "a"; a.write_bytes(b"x")
    b = tmp_path / "b"; b.write_bytes(b"y")
    with pytest.raises(merge.MergeError, match="no console attached"):
        merge.launch_difftool("anything", a, b)


# --- built-in merge tools (0.4.3): vimdiff & co. need no mergetool.<name>.cmd

def test_builtin_tool_is_usable_when_its_binary_exists(monkeypatch, tmp_path):
    """`ccs merge --tool vimdiff` on a box with vim and no git config."""
    fake = tmp_path / "bin"; fake.mkdir()
    exe = fake / ("vim.exe" if merge.sys.platform == "win32" else "vim")
    exe.write_text("", encoding="utf-8")
    if merge.sys.platform != "win32":
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake))
    monkeypatch.setattr(merge, "_git", lambda args, cwd=None: (1, ""))   # no config at all
    assert merge._tool_usable("vimdiff")
    assert merge.resolve_tool("vimdiff") == "vimdiff"
    assert merge.resolve_tool() == "vimdiff"                             # probed, no explicit
    assert "$MERGED" in merge.tool_command("vimdiff")


def test_builtin_tool_without_its_binary_says_so(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))                            # empty PATH
    monkeypatch.setattr(merge, "_git", lambda args, cwd=None: (1, ""))
    assert not merge._tool_usable("vimdiff")
    with pytest.raises(merge.MergeError, match="not on PATH"):
        merge.resolve_tool("vimdiff")
    with pytest.raises(merge.MergeError, match="need no config"):
        merge.resolve_tool()


def test_configured_cmd_wins_over_the_builtin(monkeypatch):
    monkeypatch.setattr(merge, "_git",
                        lambda args, cwd=None: (0, 'myvim "$LOCAL" "$MERGED"')
                        if args[-1] == "mergetool.vimdiff.cmd" else (1, ""))
    assert merge.tool_command("vimdiff") == 'myvim "$LOCAL" "$MERGED"'
