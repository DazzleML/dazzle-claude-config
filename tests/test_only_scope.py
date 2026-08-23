"""B7: `--only` scopes to a sub-entry path, component-wise, in every verb.

Before 0.4.3 `--only` was `entry.repo.startswith(only)`: a string prefix.
`--only dotclaude/skills/test-mutation` matched NO entry (silently), and
`--only dotclaude/sk` matched `dotclaude/skills` by accident.
"""
from __future__ import annotations

import json

import pytest

from dazzle_claude_config import merge
from dazzle_claude_config.apply import apply
from dazzle_claude_config.cli import main
from dazzle_claude_config.collect import collect
from dazzle_claude_config.manifest import Manifest
from dazzle_claude_config.syncmap import only_scope, rel_in_scope

from conftest import MANIFEST, git


# --- the predicate ---------------------------------------------------------

@pytest.mark.parametrize("only,repo,expect", [
    (None, "dotclaude/skills", (True, None)),
    ("dotclaude/skills", "dotclaude/skills", (True, None)),
    ("dotclaude", "dotclaude/skills", (True, None)),                     # parent of entries
    ("dotclaude/skills/test-mutation", "dotclaude/skills", (True, "test-mutation")),
    ("dotclaude/skills/a/SKILL.md", "dotclaude/skills", (True, "a/SKILL.md")),
    ("dotclaude/sk", "dotclaude/skills", (False, None)),                 # not a component
    ("dotclaude/skills", "dotclaude/skills-extra", (False, None)),
    ("dotclaude\\skills\\x", "dotclaude/skills", (True, "x")),           # backslashes tolerated
    ("userclaude/scripts", "dotclaude/skills", (False, None)),
    ("dotclaude/skills-extra/x", "dotclaude/skills", (False, None)),   # sibling prefix, sub-entry branch
    ("dotclaude/skills/", "dotclaude/skills", (True, None)),           # trailing slash: whole entry, never ""
])
def test_only_scope(only, repo, expect):
    assert only_scope(only, repo) == expect


def test_scope_diff_cuts_every_list():
    from dazzle_claude_config.manifest import Entry
    from dazzle_claude_config.syncmap import EntryDiff, scope_diff
    from pathlib import Path
    d = EntryDiff(entry=Entry(repo="dotclaude/skills", strategy="copy"), live_base=Path("l"),
                  repo_base=Path("r"), live_only=["a/x", "b/x"], repo_only=["a/y", "b/y"],
                  modified=["a/z", "b/z"], excluded=["a/e", "b/e"], denied_live=["a/d", "b/d"])
    s = scope_diff(d, "a")
    assert (s.live_only, s.repo_only, s.modified, s.excluded, s.denied_live) ==         (["a/x"], ["a/y"], ["a/z"], ["a/e"], ["a/d"])
    assert scope_diff(d, None) is d


def test_rel_in_scope_is_component_wise():
    assert rel_in_scope("test-mutation/SKILL.md", "test-mutation")
    assert rel_in_scope("test-mutation", "test-mutation")
    assert not rel_in_scope("test-mutation-extra/SKILL.md", "test-mutation")
    assert rel_in_scope("anything", None)


# --- a world with a directory entry holding three subtrees -------------------

@pytest.fixture
def world(env):
    """env + `dotclaude/skills` with a/, b/, and ab/ (shares a prefix with a/),
    each differing one-sidedly (checkout ahead) so apply would copy all."""
    claude, user, checkout, _, roots = env
    data = json.loads(json.dumps(MANIFEST))
    data["entries"].append({"repo": "dotclaude/skills", "territory": "dotclaude",
                            "target": "skills", "strategy": "copy"})
    (checkout / "ccs-manifest.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    for name in ("a", "b", "ab"):
        (checkout / "dotclaude" / "skills" / name).mkdir(parents=True)
        (checkout / "dotclaude" / "skills" / name / "SKILL.md").write_text(
            f"# {name} v1\n", encoding="utf-8")
        (claude / "skills" / name).mkdir(parents=True)
        (claude / "skills" / name / "SKILL.md").write_text(f"# {name} v1\n", encoding="utf-8")
    git(checkout, "add", "-A"); git(checkout, "commit", "-q", "-m", "skills v1")
    for name in ("a", "b", "ab"):
        (checkout / "dotclaude" / "skills" / name / "SKILL.md").write_text(
            f"# {name} v2\n", encoding="utf-8")
    git(checkout, "commit", "-q", "-am", "skills v2")
    return claude, user, checkout, Manifest.load(checkout), roots


def _argv(claude, user, checkout, *extra):
    return ["--no-color", "--no-fetch", "--checkout-dir", str(checkout), "--claude-dir",
            str(claude), "--user-claude", str(user), *extra]


def test_apply_only_subtree_touches_that_subtree_and_nothing_else(world, backup_dir):
    claude, user, checkout, manifest, roots = world
    r = apply(manifest, checkout, roots, backup_dir, only="dotclaude/skills/a")
    assert r.only_matched == 1
    assert [c for c in r.copied] == ["skills/a/SKILL.md"]
    assert (claude / "skills" / "a" / "SKILL.md").read_text(encoding="utf-8") == "# a v2\n"
    assert (claude / "skills" / "ab" / "SKILL.md").read_text(encoding="utf-8") == "# ab v1\n"  # not a/
    assert (claude / "skills" / "b" / "SKILL.md").read_text(encoding="utf-8") == "# b v1\n"


def test_apply_only_whole_entry_still_works(world, backup_dir):
    claude, user, checkout, manifest, roots = world
    r = apply(manifest, checkout, roots, backup_dir, only="dotclaude/skills")
    assert sorted(r.copied) == ["skills/a/SKILL.md", "skills/ab/SKILL.md", "skills/b/SKILL.md"]


def test_apply_only_partial_component_matches_nothing(world, backup_dir, capsys):
    claude, user, checkout, manifest, roots = world
    rc = main(_argv(claude, user, checkout, "apply", "--only", "dotclaude/sk", "--dry-run"))
    assert "matched no manifest entries" in capsys.readouterr().out
    assert rc == 0


def test_collect_only_subtree(world):
    claude, user, checkout, manifest, roots = world
    for name in ("a", "b"):
        (claude / "skills" / name / "SKILL.md").write_text(f"# {name} live\n", encoding="utf-8")
    # make the checkout agree with live elsewhere so collect is one-sided:
    # reset the checkout files to v1 == what live had before the edit
    for name in ("a", "b", "ab"):
        (checkout / "dotclaude" / "skills" / name / "SKILL.md").write_text(f"# {name} v1\n", encoding="utf-8")
    git(checkout, "commit", "-q", "-am", "back to v1")
    r = collect(manifest, checkout, roots, only="dotclaude/skills/b")
    assert r.copied == ["dotclaude/skills/b/SKILL.md"]
    assert (checkout / "dotclaude" / "skills" / "a" / "SKILL.md").read_text(encoding="utf-8") == "# a v1\n"


def test_guard_is_scoped_with_only(world, capsys):
    """A two-sided file OUTSIDE the --only subtree must not refuse the run;
    one INSIDE it must."""
    claude, user, checkout, manifest, roots = world
    # make skills/b two-sided: live edited, checkout ahead already (v2)
    (claude / "skills" / "b" / "SKILL.md").write_text("# b live edit\n", encoding="utf-8")
    rc = main(_argv(claude, user, checkout, "apply", "--only", "dotclaude/skills/a", "--dry-run"))
    out = capsys.readouterr().out
    assert rc == 0 and "REFUSING" not in out
    rc = main(_argv(claude, user, checkout, "apply", "--only", "dotclaude/skills/b", "--dry-run"))
    out = capsys.readouterr().out
    assert rc == 1 and "REFUSING" in out and "skills/b/SKILL.md" in out


def test_force_is_scoped_with_only(world, capsys):
    claude, user, checkout, manifest, roots = world
    (claude / "skills" / "b" / "SKILL.md").write_text("# b live edit\n", encoding="utf-8")
    (claude / "skills" / "a" / "SKILL.md").write_text("# a live edit\n", encoding="utf-8")
    rc = main(_argv(claude, user, checkout, "apply", "--only", "dotclaude/skills/a", "--force"))
    assert rc == 0
    assert (claude / "skills" / "a" / "SKILL.md").read_text(encoding="utf-8") == "# a v2\n"
    assert (claude / "skills" / "b" / "SKILL.md").read_text(encoding="utf-8") == "# b live edit\n"


def test_merge_only_reaches_a_file_inside_a_directory_entry(world):
    claude, user, checkout, manifest, roots = world
    (claude / "skills" / "b" / "SKILL.md").write_text("# b live edit\n", encoding="utf-8")
    (claude / "skills" / "a" / "SKILL.md").write_text("# a live edit\n", encoding="utf-8")
    r = merge.run(manifest, checkout, roots, only="dotclaude/skills/b", launch_tool=False, dry_run=True)
    assert [i.label for i in r.planned] == ["skills/b/SKILL.md"]


def test_two_way_labels_honours_box_tags(env):
    """A tag-gated-off entry must not be guarded (it is never applied)."""
    claude, user, checkout, _, roots = env
    data = json.loads(json.dumps(MANIFEST))
    data["entries"].append({"repo": "machines/x/machine.md", "territory": "userclaude",
                            "target": "claude-config/machine.md", "strategy": "copy",
                            "tags": ["x"]})
    (checkout / "ccs-manifest.json").write_text(json.dumps(data), encoding="utf-8")
    (checkout / "machines" / "x").mkdir(parents=True)
    (checkout / "machines" / "x" / "machine.md").write_text("repo\n", encoding="utf-8")
    git(checkout, "add", "-A"); git(checkout, "commit", "-q", "-m", "x")
    (user / "claude-config").mkdir()
    (user / "claude-config" / "machine.md").write_text("live\n", encoding="utf-8")
    manifest = Manifest.load(checkout)
    assert merge.two_way_labels(manifest, checkout, roots) == []                 # untagged: invisible
    assert merge.two_way_labels(manifest, checkout, roots, {"x"}) != []          # tagged: guarded
