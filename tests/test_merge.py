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

    The identity check therefore takes NO configuration.
    """
    item = _item(tmp_path)
    out = tmp_path / "merged"
    out.write_bytes(item.live.read_bytes())          # exactly ours
    res = merge.validate(item, out)                   # note: no probes
    assert not res.ok
    assert any("identical to ours" in f for f in res.failures)


def test_result_identical_to_theirs_is_rejected_without_probes(tmp_path):
    """The mirror: our local changes silently discarded."""
    item = _item(tmp_path)
    out = tmp_path / "merged"
    out.write_bytes(item.repo.read_bytes())          # exactly theirs
    res = merge.validate(item, out)
    assert not res.ok
    assert any("identical to theirs" in f for f in res.failures)


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
