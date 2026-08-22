"""Coverage gap found while extending the v0.4.3 box-tags-gate human checklist
(tests/checklists/v0.4.3__Feature__box-tags-gate.md, "Extend" probes 1 and 2):
`tests/test_tags_gate.py` only exercises tags on single-FILE `copy` entries.
The predicate (`entry_applies`, `syncmap.py`) is entry-shape-agnostic and
`apply`/`collect` both route every copy entry through the same `diff_all`
gate -- but `apply`'s seed-if-absent loop (`apply.py`, `manifest.seed_entries()`)
is a SEPARATE code path with its OWN `entry_applies` check, and directory
entries were never probed for tags at all. Both were confirmed correct by
manual CLI probes against a scratch world; these tests lock that in.

New file per the tester-unbounded run's constraint (existing tests are never
edited). Companion to test_tags_gate.py; does not duplicate its cases.
"""
from __future__ import annotations

import json

import pytest

from dazzle_claude_config.apply import apply
from dazzle_claude_config.collect import collect
from dazzle_claude_config.manifest import Manifest

from conftest import MANIFEST, git


@pytest.fixture
def tagged_shapes(env):
    """env + two NEW tag-gated entries the existing `tagged` fixture in
    test_tags_gate.py does not add: a DIRECTORY copy entry (two files, one
    nested) and a SEED-IF-ABSENT entry -- content present in the checkout
    only, absent from live, same shape as that fixture's single-file entry
    so delivery is actually observable (unlike retrofitting tags onto an
    existing MANIFEST entry, whose live/checkout content already matches
    in the `env` baseline and so proves nothing about gating)."""
    claude, user, checkout, _, roots = env
    data = json.loads(json.dumps(MANIFEST))
    data["entries"] += [
        {"repo": "machines/prod-vps/dir", "territory": "dotclaude",
         "target": "prod-only", "strategy": "copy", "tags": ["prod-vps"]},
        {"repo": "machines/prod-vps/seed.json", "territory": "dotclaude",
         "target": "seeded.json", "strategy": "seed-if-absent",
         "tags": ["prod-vps"]},
    ]
    (checkout / "ccs-manifest.json").write_text(json.dumps(data, indent=1),
                                                encoding="utf-8")
    (checkout / "machines" / "prod-vps" / "dir" / "sub").mkdir(parents=True)
    (checkout / "machines" / "prod-vps" / "dir" / "one.md").write_text(
        "one\n", encoding="utf-8")
    (checkout / "machines" / "prod-vps" / "dir" / "sub" / "nested.md").write_text(
        "nested\n", encoding="utf-8")
    (checkout / "machines" / "prod-vps" / "seed.json").write_text(
        '{"seed": true}\n', encoding="utf-8")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "tagged directory + seed entries")
    manifest = Manifest.load(checkout)
    return claude, user, checkout, manifest, roots


def test_directory_entry_never_delivered_without_the_tag(tagged_shapes, backup_dir):
    """apply: a tag-gated DIRECTORY entry must not deliver ANY file under it
    -- top-level or nested -- to a box that has not declared the tag."""
    claude, user, checkout, manifest, roots = tagged_shapes
    r = apply(manifest, checkout, roots, backup_dir)
    assert not (claude / "prod-only" / "one.md").exists()
    assert not (claude / "prod-only" / "sub" / "nested.md").exists()
    assert "prod-only/one.md" not in " ".join(r.copied)
    assert "prod-only/sub/nested.md" not in " ".join(r.copied)


def test_directory_entry_delivered_whole_subtree_with_the_tag(tagged_shapes, backup_dir):
    claude, user, checkout, manifest, roots = tagged_shapes
    r = apply(manifest, checkout, roots, backup_dir, box_tags={"prod-vps"})
    assert (claude / "prod-only" / "one.md").read_text(encoding="utf-8") == "one\n"
    assert (claude / "prod-only" / "sub" / "nested.md").read_text(encoding="utf-8") \
        == "nested\n"


def test_directory_entry_local_edits_never_collected_without_the_tag(tagged_shapes, backup_dir):
    """The important half for a directory entry: an edit made under a
    tag-gated directory on a box that lacks the tag must not ride `collect`
    into the shared checkout -- same guarantee HV.3 demonstrates for a
    single-file entry, extended to a directory-shaped one."""
    claude, user, checkout, manifest, roots = tagged_shapes
    apply(manifest, checkout, roots, backup_dir, box_tags={"prod-vps"})  # deliver first
    (claude / "prod-only" / "one.md").write_text("edited on this box\n",
                                                  encoding="utf-8")
    (claude / "prod-only" / "new_local_file.md").write_text("new on this box\n",
                                                            encoding="utf-8")
    collect(manifest, checkout, roots)
    assert (checkout / "machines" / "prod-vps" / "dir" / "one.md").read_text(
        encoding="utf-8") == "one\n"
    assert not (checkout / "machines" / "prod-vps" / "dir" / "new_local_file.md").exists()

    collect(manifest, checkout, roots, box_tags={"prod-vps"})
    assert (checkout / "machines" / "prod-vps" / "dir" / "one.md").read_text(
        encoding="utf-8") == "edited on this box\n"


def test_seed_if_absent_entry_not_seeded_without_the_tag(tagged_shapes, backup_dir):
    """apply's seed-if-absent loop has its own `entry_applies` check
    (manifest.seed_entries(), apply.py) -- a separate code path from the
    diff_all-driven copy loop that test_tags_gate.py's `tagged` fixture
    exercises. A tag-gated seed entry must stay un-seeded without the tag,
    even though the target is absent (the exact condition seeding fires on)."""
    claude, user, checkout, manifest, roots = tagged_shapes
    assert not (claude / "seeded.json").exists()
    r = apply(manifest, checkout, roots, backup_dir)
    assert not (claude / "seeded.json").exists()
    assert "seeded.json" not in " ".join(r.seeded)


def test_seed_if_absent_entry_seeded_with_the_tag(tagged_shapes, backup_dir):
    claude, user, checkout, manifest, roots = tagged_shapes
    r = apply(manifest, checkout, roots, backup_dir, box_tags={"prod-vps"})
    assert (claude / "seeded.json").read_text(encoding="utf-8") == '{"seed": true}\n'
    assert "seeded.json" in r.seeded
