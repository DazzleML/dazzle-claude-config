"""Coverage gap found while extending the v0.4.3 adoption-merge human
checklist (tests/checklists/v0.4.3__Feature__adoption-merge-supplied-base-and-honoured-deletions.md,
"Extend" section, and steps 1.2/1.3 as literally written).

Both tests pin CURRENT, already-shipped `validate()` behaviour discovered
while running the checklist by hand -- neither is a fix, both are a record
of what the code actually does today so a future change to either is a
deliberate decision, not a silent drift.

1. `test_replace_opcode_above_supersede_ratio_installs_untouched_seed` --
   `merge.py`'s `_SUPERSEDE_RATIO` (0.5) treats a `replace` opcode as a
   rewrite, not a loss, whenever `difflib.SequenceMatcher(old, new).ratio()`
   clears the bar. That is deliberate and exercised by the existing suite
   (`test_honoured_deletion.py::test_untouched_seed_is_never_asked_about`
   uses the sentinels "THEIRS"/"OURS", ratio 0.4, BELOW the bar). What is
   NOT covered anywhere is the opposite side of that same bar: two
   substantively different lines whose characters happen to overlap ENOUGH
   (>=0.5) install with no failure, no ask, and no signal that anything was
   chosen over anything else -- for an UNTOUCHED, never-reviewed two-way
   seed (`launch_tool=False`, no edit). This is exactly the shape of the
   checklist's own scratch-world generator
   (tests/checklists/helpers/make_scratch_v043_adopt.py, `--two-way` mode:
   sentinels "OURS-ONLY"/"THEIRS-ONLY", ratio 0.7): running checklist step
   1.3 exactly as written does NOT reproduce its documented "NOT INSTALLED"
   result, because the seed's one differing line is a `replace` opcode that
   clears 0.5. Reordering (as 1.1/1.2 instruct: "move a line") sidesteps the
   whole question by turning the SAME opcode into a `delete`, which has no
   ratio check at all (see the second test below) -- so 1.1/1.2 pass as
   documented while 1.3 does not, purely because of which opcode shape the
   diff happens to land on.

2. `test_reordering_a_kept_unique_line_past_a_neighbour_reports_it_as_lost`
   -- difflib.SequenceMatcher's greedy longest/leftmost-match tie-break, when
   a side-unique line is moved past an adjacent shared line, can align the
   shared line as the "equal" block and strand the moved line as a
   synthetic delete+insert pair -- so `validate()` reports it as
   "present only in ours ... missing from the result" even though the exact
   line is physically present in the merged output, merely at a different
   position. Found while running checklist step 1.2 (piped stdin never
   accepts): the literal instruction ("move a line") most naturally reads
   as moving the box's own unique line, which triggers this; moving a
   DIFFERENT line avoids it. No existing test exercises a reordered-but-
   present unique line.

New file per the tester-unbounded run's constraint (existing tests are
never edited, dazzle_claude_config/ is read-only).
"""
from __future__ import annotations

import difflib
from pathlib import Path

from dazzle_claude_config import merge
from dazzle_claude_config.manifest import Entry


def _item(tmp_path: Path, name: str, *, base, ours, theirs) -> merge.MergeItem:
    p = {}
    for label, text in (("base", base), ("ours", ours), ("theirs", theirs)):
        if text is None:
            p[label] = None
            continue
        f = tmp_path / f"{name}-{label}.md"
        f.write_bytes(text.encode("utf-8"))
        p[label] = f
    return merge.MergeItem(entry=Entry(repo="dotclaude/F.md", strategy="copy",
                                       territory="dotclaude", target="F.md"),
                           rel="", live=p["ours"], repo=p["theirs"], base=p["base"])


def _write(tmp_path: Path, name: str, text: str) -> Path:
    out = tmp_path / f"{name}-merged.md"
    out.write_bytes(text.encode("utf-8"))
    return out


def test_replace_opcode_above_supersede_ratio_installs_untouched_seed(tmp_path):
    """Sanity check on the constant itself, so this test fails loudly (not
    silently) if a future change moves the threshold: the checklist helper's
    own sentinels sit ABOVE it, the automated suite's sentinels sit BELOW."""
    assert difflib.SequenceMatcher(None, "THEIRS-ONLY", "OURS-ONLY").ratio() \
        >= merge._SUPERSEDE_RATIO
    assert difflib.SequenceMatcher(None, "THEIRS", "OURS").ratio() \
        < merge._SUPERSEDE_RATIO

    # No base (two-way); the seed for an untouched two-way merge is a literal
    # copy of `ours` (see merge.py's `seed()`), so `merged` here stands in
    # for that seed unedited -- nothing has been "looked at".
    ours = "shared\ncommon\nOURS-ONLY\n"
    theirs = "shared\ncommon\nTHEIRS-ONLY\n"
    item = _item(tmp_path, "sim", base=None, ours=ours, theirs=theirs)
    merged = _write(tmp_path, "sim", ours)   # the untouched seed == ours

    v = merge.validate(item, merged)
    # Currently: no failure at all. The line unique to theirs
    # ("THEIRS-ONLY") is superseded by "OURS-ONLY" (ratio 0.7 >= 0.5), so it
    # is treated as a rewrite rather than a loss, and validation passes with
    # zero human review of an untouched seed.
    assert v.ok, (
        "if this now fails, _SUPERSEDE_RATIO or the opcode classification "
        "changed -- re-check checklist 1.3 by hand against the checklist's "
        "own --two-way fixture, its expectation may now be reproducible "
        "as literally written"
    )
    assert not v.lost


def test_reordering_a_kept_unique_line_past_a_neighbour_is_not_loss(tmp_path):
    """A human-authored edit that reorders two lines -- moving a line the
    box alone holds ahead of a line both sides share -- is not a data loss
    (every original line is still present, verbatim, in the result), and
    `validate()` must not report it as one."""
    ours = "shared\ncommon\nOURS-ONLY\n"
    theirs = "shared\ncommon\nTHEIRS-ONLY\n"
    item = _item(tmp_path, "reorder", base=None, ours=ours, theirs=theirs)
    # Swap the last two lines of the seed: OURS-ONLY now precedes common.
    # Every line from `ours` is still present verbatim in this text.
    reordered = "shared\nOURS-ONLY\ncommon\n"
    merged = _write(tmp_path, "reorder", reordered)

    v = merge.validate(item, merged)
    assert not v.ok
    # The genuine loss (theirs' unique line, never present anywhere in the
    # result) is correctly reported --
    assert "theirs" in v.lost and any("THEIRS-ONLY" in ln for ln in v.lost["theirs"])
    # -- and ours' own line, present verbatim one line earlier, is NOT:
    # validate() treats a line present anywhere in the result as kept. This
    # test was written as a characterisation of the false positive and
    # flipped to an anchor when it was fixed.
    assert "ours" not in v.lost
