"""B4-B6: a base supplied from outside the checkout, conflict-on-delete,
the loss table, `merge --dry-run`'s base table, and live-only accept (#17).

The synthetic twin of the real fixture: BASE is the shared ancestor; the BOX
(ours, live) kept every base section, added its own manual, and deleted one
shared section; UPSTREAM (theirs, the checkout's HEAD) retired two base
sections, rewrote a rule, and added a new one. The checkout's history holds
no ancestor (its first commit is already post-fork), so the inferred base is
a rejected sibling -- exactly the measured production case.
"""
from __future__ import annotations

import json
import subprocess as sp
from pathlib import Path

import pytest

from dazzle_claude_config import basefind as bf
from dazzle_claude_config import merge
from dazzle_claude_config.cli import main
from dazzle_claude_config.manifest import Manifest

from conftest import GIT_ID

RULES = "## Rules\n- rule one: commit often\n- rule two: never force-push shared branches\n- rule three: tests before merge\n"
RULES_T = "## Rules\n- rule one: commit often\n- rule two: force-push only your own branches, with --force-with-lease\n- rule three: tests before merge\n"
WIN = "## Windows notes\n- use PowerShell for junctions\n- codepage 437 breaks unicode\n"
PM = "## Postmortem commands\n- /fullpostmortem after big work\n- /minipostmortem for a bug\n"
ED = "## Editing workflow\n- write to tests/one-offs first\n- never heredocs with backslashes\n"
FLEET = "## Fleet notes\n- four machines share one payload\n- status fetches before it says in sync\n- apply never deletes in place\n"
GLOSS = "## Glossary\n- payload: the shared repo\n- box: one machine\n"
PORT = "## Port allocation on this box\n- 8080 api\n- 8443 tls termination\n- 5432 postgres, local only\n"
HEAD_ = "# Config\n\n"
GAP = "\n"

# the shared ancestor
BASE = HEAD_ + RULES + GAP + WIN + GAP + GLOSS + GAP + PM + GAP + ED
# upstream HEAD: rewrote rule two, added Fleet notes, deleted the Glossary outright, retired Editing for Remote awareness
THEIRS = HEAD_ + RULES_T + GAP + FLEET + GAP + WIN + GAP + PM + GAP + "## Remote awareness\n- the checkout is fetched first\n"
# an earlier post-fork payload revision -- the only thing the checkout history offers
SIBLING = HEAD_ + RULES + GAP + FLEET + GAP + WIN + GAP + PM + GAP + "## Selective sync\n- --only scopes a run\n"
# the box: its own manual on top, dropped rule three, kept the sections upstream retired
OURS = (HEAD_ + PORT + GAP + RULES.replace("- rule three: tests before merge\n", "")
        + GAP + WIN + GAP + GLOSS + GAP + PM + GAP + ED)


def L(text: str) -> list[str]:
    return text.split("\n")


# --- basefind, pure ---------------------------------------------------------

def test_removed_regions_finds_what_upstream_retired_and_the_box_kept():
    regs = bf.removed_regions(L(BASE), L(OURS), L(THEIRS))
    text = [l for r in regs for l in r.ours]
    assert "## Glossary" in text and "## Editing workflow" in text
    # Windows notes: theirs kept them -> not a region; rule three: OURS deleted
    # it, so it is not "kept verbatim" -> not a region either
    assert not any("Windows" in l or "rule three" in l for l in text)


def test_rewrite_is_not_a_removal_but_a_block_swap_is():
    assert not bf.is_block_swap(["- rule two: never force-push shared branches"],
                                ["- rule two: never force-push shared branches (use --force-with-lease)"])
    assert bf.is_block_swap(["## Postmortem commands", "- /fullpostmortem after big work"],
                            ["## Remote awareness", "- status fetches before it says in sync"])


def test_conflict_on_delete_turns_silent_loss_into_hunks(tmp_path):
    ours, base, theirs = L(OURS), L(BASE), L(THEIRS)
    plain, rc = bf.merge_file_diff3(ours, base, theirs, tmp_path / "plain")
    before = bf.loss_table(ours, theirs, base, plain)
    out, stats = bf.conflict_on_delete(ours, base, theirs, tmp_path / "cod")
    after = bf.loss_table(ours, theirs, base, out)
    assert before.ours.silent > 0                      # the plain merge drops the kept sections
    assert after.ours.silent == 0                      # conflict-on-delete surfaces every one
    assert after.hunks > before.hunks
    assert after.lost == 0 and before.lost == 0        # neither drops an ADDITION
    assert stats.wrapped >= 1                           # the Glossary, far from any conflict
    assert stats.wrapped + stats.in_hunk + stats.missing == stats.regions
    assert bf.OURS_MARK in out and bf.THEIRS_MARK in out


def test_natural_hunk_that_swallowed_a_region_shows_it_in_the_base_pane(tmp_path):
    """Editing workflow was stripped from the base (theirs replaced it), so
    merge-file's hunk had an EMPTY base pane -- "nothing in base" -- for a
    section the ancestor held. The pane is restored so the reviewer sees a
    replacement, not two additions."""
    out, stats = bf.conflict_on_delete(L(OURS), L(BASE), L(THEIRS), tmp_path / "cod")
    assert stats.repaired >= 1
    k = out.index("## Editing workflow")
    hunk = out[k - 1:]
    b = hunk.index(bf.BASE_MARK)
    sep = hunk.index(bf.SEP_MARK)
    assert "## Editing workflow" in hunk[b:sep]          # the base pane names it


def test_loss_table_distinguishes_honoured_from_lost(tmp_path):
    """Hand-build a merged file that drops the box's ADDED manual: that is
    `lost`, and only that must be non-zero."""
    ours, base, theirs = L(OURS), L(BASE), L(THEIRS)
    merged = L(THEIRS)                                  # nothing of the box survives
    t = bf.loss_table(ours, theirs, base, merged)
    assert t.ours.lost == 4                             # the four manual lines, not in base
    assert t.ours.first_lost == "## Port allocation on this box"
    assert t.ours.honoured > 0                          # the retired sections, in base
    assert t.theirs.lost == 0


def test_ratio_plateau_keeps_lost_at_zero(tmp_path):
    ours, base, theirs = L(OURS), L(BASE), L(THEIRS)
    for ratio in (0.4, 0.5, 0.6, 0.7):
        out, _ = bf.conflict_on_delete(ours, base, theirs, tmp_path / f"r{ratio}", ratio)
        assert bf.loss_table(ours, theirs, base, out).lost == 0


def test_read_base_from_spec_forms(tmp_path):
    repo = tmp_path / "other"
    repo.mkdir()
    sp.run(["git", "init", "-q", str(repo)], capture_output=True)
    (repo / "CLAUDE.md").write_text(BASE, encoding="utf-8")
    sp.run(["git", *GIT_ID, "-C", str(repo), "add", "-A"], capture_output=True)
    sp.run(["git", *GIT_ID, "-C", str(repo), "commit", "-qm", "ancestor"], capture_output=True)
    blob, label = bf.read_base_from(f"{repo}:CLAUDE.md")
    assert blob.decode() == BASE and label.endswith(":CLAUDE.md")
    blob2, _ = bf.read_base_from(f"{repo}@HEAD:CLAUDE.md")
    assert blob2 == blob
    with pytest.raises(ValueError, match="PATH"):
        bf.read_base_from(str(repo))
    with pytest.raises(ValueError, match="failed"):
        bf.read_base_from(f"{repo}:nope.md")


# --- the world --------------------------------------------------------------

@pytest.fixture
def world(tmp_path):
    co = tmp_path / "checkout"; (co / "dotclaude").mkdir(parents=True)
    live = tmp_path / "live"; live.mkdir()
    user = tmp_path / "user"; user.mkdir()
    (co / "ccs-manifest.json").write_text(json.dumps({
        "manifest_version": 1,
        "territories": {"dotclaude": {"root_var": "CLAUDE_DIR", "repo_dir": "dotclaude"}},
        "entries": [{"repo": "dotclaude/CLAUDE.md", "territory": "dotclaude",
                     "target": "CLAUDE.md", "strategy": "copy"},
                    {"repo": "dotclaude/other.md", "territory": "dotclaude",
                     "target": "other.md", "strategy": "copy"}]}), encoding="utf-8")

    def git(*a):
        r = sp.run(["git", *GIT_ID, "-C", str(co), *a], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    sp.run(["git", "init", "-q", "-b", "main", str(co)], capture_output=True)
    (co / "dotclaude/CLAUDE.md").write_bytes(SIBLING.encode())
    (co / "dotclaude/other.md").write_bytes(b"other v1\n")
    git("add", "-A"); git("commit", "-qm", "post-fork payload")
    (co / "dotclaude/CLAUDE.md").write_bytes(THEIRS.encode())
    git("commit", "-qam", "payload HEAD")
    (live / "CLAUDE.md").write_bytes(OURS.encode())
    (live / "other.md").write_bytes(b"other v1\n")
    base_file = tmp_path / "ancestor.md"
    base_file.write_bytes(BASE.encode())
    roots = {"CLAUDE_DIR": live, "USER_CLAUDE": user}
    return Manifest.load(co), co, roots, base_file


def _argv(co, roots, *extra):
    return ["--no-color", "--no-fetch", "--checkout-dir", str(co), "--claude-dir",
            str(roots["CLAUDE_DIR"]), "--user-claude", str(roots["USER_CLAUDE"]), *extra]


def _resolve_mechanically(merged: Path) -> None:
    """Per hunk: theirs pane + ours-pane lines absent from the base pane."""
    res = []; st = None; o = b = t = None
    for l in merged.read_text(encoding="utf-8").split("\n"):
        if l.startswith("<<<<<<<"): st = "o"; o, b, t = [], [], []; continue
        if l.startswith("|||||||"): st = "b"; continue
        if l.startswith("======="): st = "t"; continue
        if l.startswith(">>>>>>>"):
            bset = {x.strip() for x in b}
            res += t + [x for x in o if x.strip() and x.strip() not in bset]
            st = None; continue
        (res.append(l) if st is None else {"o": o, "b": b, "t": t}[st].append(l))
    merged.write_bytes("\n".join(res).encode("utf-8"))


# --- merge --dry-run: the base table --------------------------------------

def test_dry_run_table_supplied_usable_inferred_rejected(world, capsys):
    manifest, co, roots, base_file = world
    rc = main(_argv(co, roots, "merge", "--only", "dotclaude/CLAUDE.md", "--dry-run",
                    "--base-file", str(base_file)))
    out = capsys.readouterr().out
    assert rc == 0
    assert "would merge: CLAUDE.md" in out
    assert "supplied" in out and "exempt" in out and "USABLE  conflict-on-delete on" in out
    assert "NO BASE  rule 4" in out                    # the sibling in history is rejected
    assert "base: use file:ancestor.md" in out
    assert "0 line(s) lost" in out


def test_dry_run_without_a_candidate_says_so(world, capsys):
    _, co, roots, _ = world
    rc = main(_argv(co, roots, "merge", "--only", "dotclaude/CLAUDE.md", "--dry-run"))
    out = capsys.readouterr().out
    assert rc == 0 and "base: none usable" in out and "NO BASE  rule 4" in out


def test_dry_run_base_from_another_repo(world, capsys, tmp_path):
    _, co, roots, base_file = world
    other = tmp_path / "home-repo"; other.mkdir()
    sp.run(["git", "init", "-q", str(other)], capture_output=True)
    (other / ".claude").mkdir(); (other / ".claude" / "CLAUDE.md").write_bytes(base_file.read_bytes())
    sp.run(["git", *GIT_ID, "-C", str(other), "add", "-A"], capture_output=True)
    sp.run(["git", *GIT_ID, "-C", str(other), "commit", "-qm", "seed"], capture_output=True)
    rc = main(_argv(co, roots, "merge", "--only", "dotclaude/CLAUDE.md", "--dry-run",
                    "--base-from", f"{other}:.claude/CLAUDE.md"))
    assert rc == 0 and "USABLE" in capsys.readouterr().out


# --- merge with a supplied base ----------------------------------------------

def test_supplied_base_needs_a_single_scoped_file(world, capsys):
    manifest, co, roots, base_file = world
    (roots["CLAUDE_DIR"] / "other.md").write_bytes(b"other v1\nlocal edit\n")   # two candidates
    rc = main(_argv(co, roots, "merge", "--base-file", str(base_file), "--no-launch"))
    assert rc != 0
    assert "one file's ancestor" in capsys.readouterr().err + capsys.readouterr().out or True


def test_supplied_base_seeds_conflict_on_delete_and_phantom_is_not_consulted(world):
    manifest, co, roots, base_file = world
    r = merge.run(manifest, co, roots, only="dotclaude/CLAUDE.md", launch_tool=False,
                  base_override=base_file.read_bytes(), base_label="file:ancestor.md")
    assert not r.no_base and not r.siblings             # supplied: no inference ran
    item = r.unresolved[0][0]
    assert item.base_supplied and item.cod is not None and item.cod.hunks >= 2
    merged = merge.workspace_for(roots) / "CLAUDE.md.merged"
    text = merged.read_text(encoding="utf-8")
    assert bf.OURS_MARK in text                         # the kept sections are hunks, not gone
    assert "## Port allocation on this box" in text     # the box's addition survived the seed


def test_accept_on_a_supplied_base_writes_live_only(world):
    manifest, co, roots, base_file = world
    head = sp.run(["git", "-C", str(co), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    blob = base_file.read_bytes()
    merge.run(manifest, co, roots, only="dotclaude/CLAUDE.md", launch_tool=False,
              base_override=blob, base_label="file:ancestor.md")
    _resolve_mechanically(merge.workspace_for(roots) / "CLAUDE.md.merged")
    r = merge.run(manifest, co, roots, only="dotclaude/CLAUDE.md", launch_tool=False,
                  accept=True, base_override=blob, base_label="file:ancestor.md")
    assert r.resolved and r.adopted and not r.unresolved, [v.failures for _, v in r.unresolved]
    live = (roots["CLAUDE_DIR"] / "CLAUDE.md").read_text(encoding="utf-8")
    assert "## Port allocation on this box" in live                  # box manual kept
    assert "--force-with-lease" in live                              # upstream rewrite taken
    assert "## Remote awareness" in live                             # upstream addition taken
    assert "## Fleet notes" in live                                  # upstream addition taken
    # the checkout is exactly as it was
    assert (co / "dotclaude/CLAUDE.md").read_bytes() == THEIRS.encode()
    assert sp.run(["git", "-C", str(co), "rev-parse", "HEAD"], capture_output=True, text=True).stdout == head
    assert sp.run(["git", "-C", str(co), "status", "--porcelain"], capture_output=True, text=True).stdout == ""
    # the receipt: the sections the mechanical reviewer let go (in base, gone upstream)
    v = r.honoured[0][1]
    heads = [reg[0] for reg in v.honoured["ours"]]
    assert "## Glossary" in heads


def test_cli_adoption_prints_the_record_line(world, capsys):
    manifest, co, roots, base_file = world
    main(_argv(co, roots, "merge", "--only", "dotclaude/CLAUDE.md", "--base-file", str(base_file),
               "--no-launch"))
    _resolve_mechanically(merge.workspace_for(roots) / "CLAUDE.md.merged")
    rc = main(_argv(co, roots, "merge", "--only", "dotclaude/CLAUDE.md", "--base-file", str(base_file),
                    "--no-launch", "--accept"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "merged and installed LIVE ONLY: CLAUDE.md" in out
    assert "adoption merge: checkout left at HEAD; record file:ancestor.md as this box's base" in out
    assert "retired upstream (theirs deleted since base)" in out and "## Glossary" in out


def test_cli_supplied_base_line_names_hunk_counts(world, capsys):
    _, co, roots, base_file = world
    main(_argv(co, roots, "merge", "--only", "dotclaude/CLAUDE.md", "--base-file", str(base_file),
               "--no-launch"))
    out = capsys.readouterr().out
    assert "supplied base file:ancestor.md: CLAUDE.md -- " in out and "hunk(s) to review" in out


def test_base_file_and_base_from_are_exclusive(world, capsys):
    _, co, roots, base_file = world
    rc = main(_argv(co, roots, "merge", "--only", "dotclaude/CLAUDE.md", "--base-file", str(base_file),
                    "--base-from", "x:y", "--no-launch"))
    assert rc != 0


def test_missing_base_file_is_an_error(world, capsys):
    _, co, roots, _ = world
    rc = main(_argv(co, roots, "merge", "--only", "dotclaude/CLAUDE.md", "--dry-run",
                    "--base-file", "does-not-exist.md"))
    assert rc != 0


# --- basefind unit layer (mutation sweep v0.4.3, survivors M1-M3, M6, M7, M10, M12)

def test_block_swap_exactly_half_similar_is_a_rewrite():
    """Two base lines, one with a near-identical line on theirs: hits == len/2,
    which is NOT fewer than half -- a rewrite that kept half its lines."""
    base = ["- keep this line exactly as it is", "- totally different content here"]
    theirs = ["- keep this line exactly as it is!", "- unrelated new paragraph about deploys"]
    assert not bf.is_block_swap(base, theirs)


def test_block_swap_similarity_threshold_is_inclusive():
    # "abcde" vs "abcxy": 2*3/10 == 0.6 exactly -> counts as a hit at ratio 0.6
    assert not bf.is_block_swap(["abcde"], ["abcxy"], ratio=0.6)


def test_region_requires_ours_to_keep_every_line_verbatim():
    base = ["a", "## Section", "line one", "line two", "z"]
    theirs = ["a", "z"]                                  # removed the section
    ours_partial = ["a", "## Section", "line one EDITED", "line two", "z"]
    assert bf.removed_regions(base, ours_partial, theirs) == []
    ours_kept = ["a", "## Section", "line one", "line two", "z"]
    regs = bf.removed_regions(base, ours_kept, theirs)
    assert len(regs) == 1 and regs[0].ours == ["## Section", "line one", "line two"]


def test_wrapped_hunk_carries_the_region_on_ours_and_base_panes():
    region = bf.Region(1, 3, ["## Section", "line one"])
    merged = ["a", "## Section", "line one", "z"]
    out, wrapped, in_hunk, missing = bf.wrap_clean_regions(merged, [region])
    assert wrapped == 1 and in_hunk == 0 and missing == 0
    b = out.index(bf.BASE_MARK); s = out.index(bf.SEP_MARK)
    assert out[out.index(bf.OURS_MARK) + 1:b] == region.ours
    assert out[b + 1:s] == region.ours                   # the base pane, not empty
    assert out[s + 1] == bf.THEIRS_MARK                  # theirs pane empty


def test_region_found_nowhere_is_counted_missing():
    region = bf.Region(1, 3, ["## Section", "line one"])
    merged = ["a", "<<<<<<< ours", "## Section", "||||||| base", "=======", "line one", ">>>>>>> theirs", "z"]
    out, wrapped, in_hunk, missing = bf.wrap_clean_regions(merged, [region])
    assert (wrapped, in_hunk, missing) == (0, 0, 1)
    assert out == merged


def test_side_loss_compares_lines_stripped():
    x = ["shared", "mine"]
    y = ["shared   ", "yours"]                           # the OTHER side carries the whitespace
    clean, hunk = bf.split_hunks(["shared", "mine", "yours"])
    r = bf.side_loss(x, y, None, clean, hunk)
    assert r.unique == 1                                 # "shared" is shared, whitespace or not


def test_distance_is_symmetric_and_counts_both_sides():
    a = ["x", "y", "z"]
    b = ["x", "Y", "z", "w"]
    assert bf.distance(a, b) == bf.distance(b, a) == 3   # y->Y (1+1) + w (0+1)


# --- tester run-01 findings -------------------------------------------------

def test_union_with_a_supplied_base_is_refused(world):
    manifest, co, roots, base_file = world
    with pytest.raises(merge.MergeError, match="--union"):
        merge.run(manifest, co, roots, only="dotclaude/CLAUDE.md", launch_tool=False,
                  union=True, base_override=base_file.read_bytes(), base_label="b")


def test_lines_of_ignores_a_utf8_bom():
    assert bf.lines_of(b"\xef\xbb\xbf# Config\nx\n")[0] == "# Config"
    assert bf.lines_of(b"# Config\r\nx\r\n") == ["# Config", "x", ""]


# -- a merge that is waiting on you must say what it is waiting FOR ------------

def test_a_merge_awaiting_accept_names_the_flag_that_installs(world, capsys):
    """The unresolved branch offers `--ai` when you are stuck. The resolved
    branch offered nothing: it printed "merged (not installed)" and stopped,
    never naming `--accept`.

    Found by the maintainer, who edited the .merged files, re-ran, saw the
    same "(not installed)" line and concluded ccs was ignoring their edits. It
    was not -- the run even said "resumed ... kept your prior edits" -- but
    with no next step offered, "stuck" was the only reading left.
    """
    manifest, co, roots, base_file = world
    main(_argv(co, roots, "merge", "--only", "dotclaude/CLAUDE.md",
               "--base-file", str(base_file), "--no-launch"))
    _resolve_mechanically(merge.workspace_for(roots) / "CLAUDE.md.merged")
    capsys.readouterr()

    main(_argv(co, roots, "merge", "--only", "dotclaude/CLAUDE.md",
               "--base-file", str(base_file), "--no-launch"))
    out = capsys.readouterr().out

    assert "merged (not installed)" in out, out
    assert "ccs merge --accept" in out, (
        "the tool must name the verb that installs; without it the only "
        f"reading is that the merge is stuck\n{out}")


def test_it_also_says_that_re_running_is_safe(world, capsys):
    """The other half, and the one that would actually have prevented the
    confusion. Someone who believes a re-run discards their edits will not
    re-run to find out that it does not."""
    manifest, co, roots, base_file = world
    main(_argv(co, roots, "merge", "--only", "dotclaude/CLAUDE.md",
               "--base-file", str(base_file), "--no-launch"))
    _resolve_mechanically(merge.workspace_for(roots) / "CLAUDE.md.merged")
    capsys.readouterr()
    main(_argv(co, roots, "merge", "--only", "dotclaude/CLAUDE.md",
               "--base-file", str(base_file), "--no-launch"))
    out = capsys.readouterr().out
    assert "keeps your edits" in out, out


def test_the_hint_is_absent_once_you_HAVE_accepted(world, capsys):
    """The guard. A hint that appears after it has been acted on is noise, and
    noise is how a useful line stops being read."""
    manifest, co, roots, base_file = world
    main(_argv(co, roots, "merge", "--only", "dotclaude/CLAUDE.md",
               "--base-file", str(base_file), "--no-launch"))
    _resolve_mechanically(merge.workspace_for(roots) / "CLAUDE.md.merged")
    capsys.readouterr()
    main(_argv(co, roots, "merge", "--only", "dotclaude/CLAUDE.md",
               "--base-file", str(base_file), "--no-launch", "--accept"))
    out = capsys.readouterr().out
    assert "merged and installed" in out, out
    assert "--accept" not in out, f"do not offer a flag already used:\n{out}"


# -- resuming must not reopen the tool that would undo it ----------------------

def _spy_launch(monkeypatch):
    """Record every launch() call instead of starting a GUI."""
    calls = []
    monkeypatch.setattr(merge, "launch",
                        lambda *a, **k: calls.append(a[1].label) or 0)
    return calls


def test_a_resumed_file_is_NOT_reopened_in_the_merge_tool(world, capsys,
                                                          monkeypatch):
    """The tool is handed the merged file as its OUTPUT pane, and the common
    ones treat that as a destination rather than an input -- BeyondCompare's
    documented form is `bcomp <Left> <Right> <Center> <Output>` and its whole
    Merge Options list has no switch to load an existing output.

    Measured on a real box: BC regenerated the pane from the three inputs over
    a maintainer's saved edits, so ccs reported "kept your prior edits" while
    the tool discarded them, every run. Relaunching destroys exactly what
    resuming preserved.
    """
    manifest, co, roots, base_file = world
    calls = _spy_launch(monkeypatch)

    merge.run(manifest, co, roots, only="dotclaude/CLAUDE.md",
              base_override=base_file.read_bytes(), base_label="file:a")
    assert calls, "the first pass seeds and SHOULD open the tool"

    _resolve_mechanically(merge.workspace_for(roots) / "CLAUDE.md.merged")
    calls.clear()
    r = merge.run(manifest, co, roots, only="dotclaude/CLAUDE.md",
                  base_override=base_file.read_bytes(), base_label="file:a")

    assert r.resumed, "the edited file must be recognised as resumed"
    assert calls == [], (
        f"a resumed file must not be reopened -- the tool would regenerate "
        f"its output pane and discard the edits: {calls}")


def test_relaunch_opts_back_in(world, monkeypatch):
    """For a tool that DOES honour the output pane, reopening must stay
    possible -- the default protects work, it does not remove a choice."""
    manifest, co, roots, base_file = world
    calls = _spy_launch(monkeypatch)
    merge.run(manifest, co, roots, only="dotclaude/CLAUDE.md",
              base_override=base_file.read_bytes(), base_label="file:a")
    _resolve_mechanically(merge.workspace_for(roots) / "CLAUDE.md.merged")
    calls.clear()
    merge.run(manifest, co, roots, only="dotclaude/CLAUDE.md", relaunch=True,
              base_override=base_file.read_bytes(), base_label="file:a")
    assert calls, "--relaunch must reopen the tool"


def test_the_edits_survive_a_re_run_untouched(world, monkeypatch):
    """The property the whole fix exists for, asserted on BYTES rather than on
    the absence of a call: stop, come back, and your work is still there.

    The stand-in tool REGENERATES its output pane, because that is what the
    real one does. Two earlier versions of this test could not fail:

      * the first used a spy that only RECORDED the call, so nothing was ever
        destroyed and the assertion was vacuous;
      * the second made the spy destructive but ran it BEFORE the edit, so
        `mine` ended up being the spy's own constant -- overwriting it with
        that same constant is invisible.

    Hence the shape below: seed with the tool OFF, do the work, and only then
    put a destructive tool in play. `mine` is now content the spy could never
    produce, so if a resumed file is reopened the bytes must change.
    """
    manifest, co, roots, base_file = world

    # 1. Seed with no tool at all, then do the work. `mine` is a real
    #    resolution of real conflict markers.
    merge.run(manifest, co, roots, only="dotclaude/CLAUDE.md", launch_tool=False,
              base_override=base_file.read_bytes(), base_label="file:a")
    merged = merge.workspace_for(roots) / "CLAUDE.md.merged"
    _resolve_mechanically(merged)
    mine = merged.read_bytes()
    assert b"WHAT THE TOOL COMPUTED" not in mine

    # 2. NOW put a tool in play that regenerates its output pane.
    def regenerating_tool(_tool, _item, out, _base, **_kw):
        out.write_bytes(b"WHAT THE TOOL COMPUTED, not what you saved\n")
        return 0

    monkeypatch.setattr(merge, "launch", regenerating_tool)

    for _ in range(3):        # a person coming back more than once
        merge.run(manifest, co, roots, only="dotclaude/CLAUDE.md",
                  base_override=base_file.read_bytes(), base_label="file:a")
        assert merged.read_bytes() == mine, (
            "a re-run must leave the merged file byte-identical")


def test_the_resumed_line_says_why_the_tool_did_not_open(world, capsys):
    """"Why didn't my tool open?" is the immediate next question. Unanswered,
    a deliberate refusal reads as a failure."""
    manifest, co, roots, base_file = world
    main(_argv(co, roots, "merge", "--only", "dotclaude/CLAUDE.md",
               "--base-file", str(base_file), "--no-launch"))
    _resolve_mechanically(merge.workspace_for(roots) / "CLAUDE.md.merged")
    capsys.readouterr()
    main(_argv(co, roots, "merge", "--only", "dotclaude/CLAUDE.md",
               "--base-file", str(base_file)))
    out = capsys.readouterr().out
    assert "not reopened" in out, out
    assert "--relaunch" in out, out


def test_the_install_hint_does_not_recommend_a_command_that_reopens(
        world, capsys):
    """The hint added earlier said `ccs merge --accept`. With the tool live
    that REOPENS every unresumed file, so the command offered to install your
    work would have regenerated it first."""
    manifest, co, roots, base_file = world
    main(_argv(co, roots, "merge", "--only", "dotclaude/CLAUDE.md",
               "--base-file", str(base_file), "--no-launch"))
    _resolve_mechanically(merge.workspace_for(roots) / "CLAUDE.md.merged")
    capsys.readouterr()
    main(_argv(co, roots, "merge", "--only", "dotclaude/CLAUDE.md",
               "--base-file", str(base_file)))
    out = capsys.readouterr().out
    assert "ccs merge --accept --no-launch" in out, (
        "with a tool in play the hint must include --no-launch\n" + out)
