"""B3: the validator consults the base (#16), and without a base the human
who reviewed the file is asked before a dropped line becomes a refusal.

Fixture vocabulary: BASE is the common ancestor; OURS is live, THEIRS is the
checkout. A line in BASE that one side removed and the other kept is a
deletion the merge HONOURS; a line one side ADDED (not in BASE) that the
result lacks is LOST.
"""
from __future__ import annotations

from pathlib import Path

from dazzle_claude_config import merge
from dazzle_claude_config.manifest import Entry, Manifest

BASE = "# Config\n\nrule A\nrule B\n\n## Legacy notes on the foo service\nthe foo service restarts nightly at 03:00\nits logs live under the operator home\n"
# upstream (theirs) retired "## Legacy notes on the foo service"; the box (ours) kept it and added a rule
THEIRS = "# Config\n\nrule A\nrule B\n\n## Guidance for bar deployments\nprefer blue-green cutovers with a canary\n"
OURS = "# Config\n\nrule A\nrule B\nrule C added here\n\n## Legacy notes on the foo service\nthe foo service restarts nightly at 03:00\nits logs live under the operator home\n"


def _item(tmp_path: Path, base=BASE, ours=OURS, theirs=THEIRS):
    p = {}
    for name, text in (("base", base), ("ours", ours), ("theirs", theirs)):
        if text is None:
            p[name] = None
            continue
        f = tmp_path / f"{name}.md"
        f.write_bytes(text.encode("utf-8"))
        p[name] = f
    return merge.MergeItem(entry=Entry(repo="dotclaude/CLAUDE.md", strategy="copy",
                                       territory="dotclaude", target="CLAUDE.md"),
                           rel="", live=p["ours"], repo=p["theirs"], base=p["base"])


def _write(tmp_path, text):
    out = tmp_path / "merged.md"
    out.write_bytes(text.encode("utf-8"))
    return out


# --- the rule ------------------------------------------------------------

def test_upstream_retirement_is_honoured_with_a_base(tmp_path):
    """The #16 case: git drops the section theirs retired; our addition
    survives. Before B3 this result was refused every time."""
    item = _item(tmp_path)
    merged = _write(tmp_path, "# Config\n\nrule A\nrule B\nrule C added here\n\n"
                              "## Guidance for bar deployments\nprefer blue-green cutovers with a canary\n")
    v = merge.validate(item, merged)
    assert v.ok, v.failures
    assert list(v.honoured) == ["ours"]            # lines only in ours, absent from result
    region = v.honoured["ours"][0]
    assert region[0] == "## Legacy notes on the foo service"
    assert len(region) == 3
    assert v.lost == {}


def test_box_side_retirement_is_honoured_too(tmp_path):
    """Mirror: the box deleted a base section upstream still carries."""
    base = BASE
    ours = "# Config\n\nrule A\nrule B\n"                       # box removed Old section
    theirs = BASE + "\n## Upstream extra\nmore\n"                # upstream kept it, added
    item = _item(tmp_path, base=base, ours=ours, theirs=theirs)
    merged = _write(tmp_path, "# Config\n\nrule A\nrule B\n\n## Upstream extra\nmore\n")
    v = merge.validate(item, merged)
    assert v.ok, v.failures
    assert list(v.honoured) == ["theirs"]
    assert v.honoured["theirs"][0][0] == "## Legacy notes on the foo service"


def test_a_dropped_addition_still_fails_with_a_base(tmp_path):
    """What the gate is FOR: ours added 'rule C' (not in base); the result
    lacks it. That is loss, base or no base."""
    item = _item(tmp_path)
    merged = _write(tmp_path, THEIRS)                            # exactly theirs
    v = merge.validate(item, merged)
    assert not v.ok
    assert v.lost["ours"] == ["rule C added here"]
    assert v.honoured["ours"][0][0] == "## Legacy notes on the foo service"          # reported alongside
    assert any(f.startswith("dropped:") for f in v.failures)
    assert v.only_loss


def test_honoured_means_absent_from_the_other_side(tmp_path):
    """A base line BOTH sides still have cannot be an honoured deletion; if
    the result lacks it, that is loss (guarded by the earlier 'other side
    has it' filter -- pinned here so the base rule cannot widen it)."""
    item = _item(tmp_path, base=BASE, ours=BASE + "ours add\n", theirs=BASE + "theirs add\n")
    merged = _write(tmp_path, "# Config\n\nrule A\n\nours add\ntheirs add\n")   # rule B gone
    v = merge.validate(item, merged)
    # rule B is in base AND in both sides -> not "only in" either side, so the
    # per-side check never sees it; the probes/identity checks are what catch
    # a shared line vanishing. The assertion here is only that the base rule
    # did not mark it honoured.
    assert v.honoured == {}


def test_without_a_base_behaviour_is_unchanged(tmp_path):
    item = _item(tmp_path, base=None)
    merged = _write(tmp_path, THEIRS)
    v = merge.validate(item, merged)
    assert not v.ok and v.honoured == {}
    assert "rule C added here" in v.lost["ours"]
    assert "## Legacy notes on the foo service" in v.lost["ours"]                    # no base: cannot tell


def test_only_loss_is_false_when_other_failures_exist(tmp_path):
    item = _item(tmp_path, base=None)
    merged = _write(tmp_path, THEIRS + "<<<<<<< ours\nstray\n")
    v = merge.validate(item, merged)
    assert not v.ok and not v.only_loss


# --- the ask, end to end through run() -----------------------------------

def _two_way_world(tmp_path):
    """A checkout with ONE commit (so no base can be inferred) whose HEAD and
    live file each hold a unique line."""
    import subprocess as sp
    from conftest import GIT_ID
    co = tmp_path / "checkout"; (co / "dotclaude").mkdir(parents=True)
    live = tmp_path / "live"; live.mkdir()
    uc = tmp_path / "uc"; uc.mkdir()
    sp.run(["git", "init", "-q", str(co)], capture_output=True)
    (co / "ccs-manifest.json").write_text(
        '{"manifest_version":1,'
        '"territories":{"dotclaude":{"root_var":"CLAUDE_DIR","repo_dir":"dotclaude"}},'
        '"entries":[{"repo":"dotclaude/F.md","territory":"dotclaude",'
        '"target":"F.md","strategy":"copy"}]}', encoding="utf-8")
    (co / "dotclaude/F.md").write_bytes(b"shared\ncommon\nTHEIRS\n")
    r = sp.run(["git", *GIT_ID, "-C", str(co), "add", "-A"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    r = sp.run(["git", *GIT_ID, "-C", str(co), "commit", "-qm", "only"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    (live / "F.md").write_bytes(b"shared\ncommon\nOURS\n")
    roots = {"CLAUDE_DIR": live, "USER_CLAUDE": uc}
    return Manifest.load(co), co, roots


def _resolve_by_hand(manifest, co, roots, text: bytes):
    """First run seeds the workspace (no tool); the human edits the result."""
    r = merge.run(manifest, co, roots, launch_tool=False)
    assert r.no_base and not r.resolved
    ws = merge.workspace_for(roots)
    merged = next(p for p in ws.rglob("F.md.merged"))
    merged.write_bytes(text)
    return merged


def test_no_base_dropped_line_asks_and_installs_on_yes(tmp_path):
    manifest, co, roots = _two_way_world(tmp_path)
    # The edit must DIFFER from the seed (with no base the seed IS ours): a
    # result equal to the seed reads as "never looked" and is re-seeded. A
    # reorder keeps every line known (nothing invented) and drops THEIRS.
    _resolve_by_hand(manifest, co, roots, b"common\nshared\nOURS\n")
    asked = []
    r = merge.run(manifest, co, roots, launch_tool=False, accept=True,
                  confirm_loss=lambda item, v: asked.append(v.lost) or True)
    assert asked and asked[0] == {"theirs": ["THEIRS"]}
    assert r.accepted_with_loss and r.resolved and not r.unresolved
    assert (roots["CLAUDE_DIR"] / "F.md").read_bytes() == b"common\nshared\nOURS\n"
    assert (co / "dotclaude/F.md").read_bytes() == b"common\nshared\nOURS\n"


def test_no_base_dropped_line_refused_on_no(tmp_path):
    manifest, co, roots = _two_way_world(tmp_path)
    _resolve_by_hand(manifest, co, roots, b"common\nshared\nOURS\n")
    r = merge.run(manifest, co, roots, launch_tool=False, accept=True,
                  confirm_loss=lambda item, v: False)
    assert r.unresolved and not r.resolved and not r.accepted_with_loss
    assert (co / "dotclaude/F.md").read_bytes() == b"shared\ncommon\nTHEIRS\n"


def test_no_base_default_never_accepts_without_a_console(tmp_path, monkeypatch):
    """CI / piped stdin: the question cannot be asked, so the answer is no."""
    monkeypatch.setenv("CI", "1")
    manifest, co, roots = _two_way_world(tmp_path)
    _resolve_by_hand(manifest, co, roots, b"common\nshared\nOURS\n")
    r = merge.run(manifest, co, roots, launch_tool=False, accept=True)
    assert r.unresolved and not r.accepted_with_loss


def test_untouched_seed_is_never_asked_about(tmp_path):
    """The ask is for a HUMAN's result. With no base the seed IS ours, so it
    always fails validation (theirs dropped) -- and must never prompt: the
    person has not looked yet. (Launching a tool is what says they did.)"""
    manifest, co, roots = _two_way_world(tmp_path)
    asked = []
    merge.run(manifest, co, roots, launch_tool=False,
              confirm_loss=lambda item, v: asked.append(1) or True)
    assert not asked


def test_no_base_ask_does_not_apply_when_a_base_exists(tmp_path):
    """With a base the rule decides; the human is not asked to bless loss."""
    item = _item(tmp_path)
    assert item.base is not None
    merged = _write(tmp_path, THEIRS)
    v = merge.validate(item, merged)
    assert not v.ok and v.only_loss
    # run() gates the ask on `item.base is None`; pin the predicate directly
    assert not (item.base is None and v.only_loss)


# --- with a base, end to end: installs; and the tripwire -------------------

def _history_world(tmp_path):
    """Two commits: the first is the common ancestor (live still equals it
    apart from its own addition); HEAD retired a section. infer_base finds
    the first commit, so validation runs WITH a base."""
    import subprocess as sp
    from conftest import GIT_ID
    co = tmp_path / "checkout"; (co / "dotclaude").mkdir(parents=True)
    live = tmp_path / "live"; live.mkdir()
    uc = tmp_path / "uc"; uc.mkdir()
    sp.run(["git", "init", "-q", str(co)], capture_output=True)
    (co / "ccs-manifest.json").write_text(
        '{"manifest_version":1,'
        '"territories":{"dotclaude":{"root_var":"CLAUDE_DIR","repo_dir":"dotclaude"}},'
        '"entries":[{"repo":"dotclaude/F.md","territory":"dotclaude",'
        '"target":"F.md","strategy":"copy"}]}', encoding="utf-8")

    def git(*a):
        r = sp.run(["git", *GIT_ID, "-C", str(co), *a], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    (co / "dotclaude/F.md").write_bytes(BASE.encode())
    git("add", "-A"); git("commit", "-qm", "ancestor")
    (co / "dotclaude/F.md").write_bytes(THEIRS.encode())        # upstream retired the section
    git("commit", "-qam", "retire")
    (live / "F.md").write_bytes(OURS.encode())                  # box kept it, added rule C
    return Manifest.load(co), co, {"CLAUDE_DIR": live, "USER_CLAUDE": uc}


def test_retirement_installs_end_to_end_with_an_inferred_base(tmp_path):
    """#16 AC-1: the retired section is gone, the box's addition is kept, and
    --accept installs on both sides. Before B3: refused."""
    manifest, co, roots = _history_world(tmp_path)
    r = merge.run(manifest, co, roots, launch_tool=False, accept=True)
    assert not r.no_base, "the ancestor commit must be found"
    assert r.resolved and not r.unresolved, [v.failures for _, v in r.unresolved]
    installed = (roots["CLAUDE_DIR"] / "F.md").read_text(encoding="utf-8")
    assert "rule C added here" in installed
    assert "## Legacy notes on the foo service" not in installed
    assert (co / "dotclaude/F.md").read_text(encoding="utf-8") == installed
    assert r.honoured and r.honoured[0][1].honoured["ours"][0][0] == "## Legacy notes on the foo service"


def test_tripwire_a_merged_output_used_as_base_names_the_boxs_own_heading(tmp_path, capsys):
    """#16 AC-5 / M8: if someone records the MERGED output as the base, the
    box's own section is "in the base" and a later upstream copy without it
    reads as an upstream retirement -- validation passes (it cannot know) and
    the print is the only thing that can catch it: it names the heading."""
    from dazzle_claude_config import cli, render
    render.init(True)
    box_section = "## Port allocation on this box\n8080 api, 8443 tls\n"
    merged_as_base = "# Config\n\nrule A\nrule B\n\n" + box_section          # the wrong base
    ours = merged_as_base                                                    # box file
    theirs = "# Config\n\nrule A\nrule B\n\n## Upstream news\nnew rule\n"    # never had the section
    item = _item(tmp_path, base=merged_as_base, ours=ours, theirs=theirs)
    merged = _write(tmp_path, theirs)                                        # section silently gone
    v = merge.validate(item, merged)
    assert v.ok                                                              # it cannot know...
    cli._print_honoured(v)
    out = capsys.readouterr().out
    assert "retired upstream (theirs deleted since base)" in out             # ...but it says so
    assert "## Port allocation on this box" in out
