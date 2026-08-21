"""collect: guard-stack behavior including A1 (refusals) and A8 (index check)."""
import dataclasses

from dazzle_claude_config.collect import collect
from dazzle_claude_config.gitops import CheckoutRepo


def test_clean_env_collects_nothing(env):
    _, _, checkout, manifest, roots = env
    r = collect(manifest, checkout, roots)
    assert r.copied == [] and r.refusals == 0


def test_new_and_modified_files_collected(env):
    claude, user, checkout, manifest, roots = env
    (claude / "agents" / "newbie.md").write_text("new agent\n", encoding="utf-8")
    (claude / "CLAUDE.md").write_text("# global memory v2\n", encoding="utf-8")
    r = collect(manifest, checkout, roots)
    assert "dotclaude/agents/newbie.md" in r.copied
    assert "dotclaude/CLAUDE.md" in r.copied
    assert (checkout / "dotclaude" / "agents" / "newbie.md").read_text(
        encoding="utf-8") == "new agent\n"
    assert (checkout / "dotclaude" / "CLAUDE.md").read_text(
        encoding="utf-8") == "# global memory v2\n"


def test_dry_run_copies_nothing(env):
    claude, _, checkout, manifest, roots = env
    (claude / "agents" / "newbie.md").write_text("new agent\n", encoding="utf-8")
    r = collect(manifest, checkout, roots, dry_run=True)
    assert "dotclaude/agents/newbie.md" in r.copied
    assert not (checkout / "dotclaude" / "agents" / "newbie.md").exists()


def test_only_scopes_collect_to_one_entry(env):
    """AC-1: --only must leave non-matching entries entirely untouched.

    Without this, the only way to publish part of a payload was to publish
    all of it -- which measured out at 13 personal files bound for a public
    repo (DWP-7 GT-6).
    """
    claude, _, checkout, manifest, roots = env
    (claude / "agents" / "newbie.md").write_text("new agent\n", encoding="utf-8")
    (claude / "CLAUDE.md").write_text("# global memory v2\n", encoding="utf-8")
    r = collect(manifest, checkout, roots, only="dotclaude/agents")
    assert "dotclaude/agents/newbie.md" in r.copied
    assert "dotclaude/CLAUDE.md" not in r.copied
    assert not (checkout / "dotclaude" / "CLAUDE.md").read_text(
        encoding="utf-8").startswith("# global memory v2")


def test_only_matching_nothing_is_reported_not_silent(env):
    """AC-2: a prefix that matches no entry must be visible, not a quiet 0.

    A typo'd --only otherwise reports success having copied nothing, which
    reads identically to 'already in sync'.
    """
    claude, _, checkout, manifest, roots = env
    (claude / "agents" / "newbie.md").write_text("new agent\n", encoding="utf-8")
    r = collect(manifest, checkout, roots, only="dotclaude/nope")
    assert r.only_matched == 0
    assert r.copied == []


def test_only_none_still_processes_every_entry(env):
    """AC-1 control: the default path must be unchanged by --only's addition."""
    claude, _, checkout, manifest, roots = env
    (claude / "agents" / "newbie.md").write_text("new agent\n", encoding="utf-8")
    (claude / "CLAUDE.md").write_text("# global memory v2\n", encoding="utf-8")
    r = collect(manifest, checkout, roots)
    assert r.only_matched > 1
    assert "dotclaude/agents/newbie.md" in r.copied
    assert "dotclaude/CLAUDE.md" in r.copied


def test_hold_additions_withholds_new_files(env):
    """AC-4: on a payload that declares hold_additions, a new live file is NOT
    copied and IS named.

    Measured motivation (DWP-7 GT-6): an unscoped collect toward the public
    payload would have published 13 personal files, and the only thing that
    stopped it was an unrelated CLAUDE.md refusal.
    """
    claude, _, checkout, manifest, roots = env
    strict = dataclasses.replace(manifest, hold_additions=True)
    (claude / "agents" / "personal-thing.md").write_text("private\n", encoding="utf-8")
    (claude / "CLAUDE.md").write_text("# global memory v2\n", encoding="utf-8")
    r = collect(strict, checkout, roots)
    assert "dotclaude/agents/personal-thing.md" in r.withheld_additions
    assert "dotclaude/agents/personal-thing.md" not in r.copied
    assert not (checkout / "dotclaude" / "agents" / "personal-thing.md").exists()
    # a file the checkout ALREADY tracks still updates -- gating additions must
    # not freeze the payload
    assert "dotclaude/CLAUDE.md" in r.copied


def test_hold_additions_add_flag_overrides(env):
    """AC-5: --add is the escape hatch, or the gate is a wall."""
    claude, _, checkout, manifest, roots = env
    strict = dataclasses.replace(manifest, hold_additions=True)
    (claude / "agents" / "wanted.md").write_text("share me\n", encoding="utf-8")
    r = collect(strict, checkout, roots, add=True)
    assert "dotclaude/agents/wanted.md" in r.copied
    assert r.withheld_additions == []


def test_hold_additions_permits_first_run_adoption(env, tmp_path):
    """AC-6: an entry the checkout carries nothing for is adoption, not drift.

    Without this, the FIRST collect against a fresh payload would withhold
    everything and report success -- a silent no-op (DWP-4: adoption creates
    the base).
    """
    claude, _, checkout, manifest, roots = env
    strict = dataclasses.replace(manifest, hold_additions=True)
    import shutil as _sh
    _sh.rmtree(checkout / "dotclaude" / "agents")
    (claude / "agents" / "first.md").write_text("first\n", encoding="utf-8")
    r = collect(strict, checkout, roots)
    assert "dotclaude/agents/first.md" in r.copied
    assert "dotclaude/agents" in r.adopted_entries


def test_hold_additions_defaults_off(env):
    """AC-11: a manifest without hold_additions behaves exactly as before.

    Both live payload repos predate this field; a default of True would have
    silently frozen them.
    """
    _, _, _, manifest, _ = env
    assert manifest.hold_additions is False


def test_a1_planted_secret_refused(env):
    claude, _, checkout, manifest, roots = env
    planted = "key = sk-ant-api03-" + "z" * 24 + "\n"
    (claude / "agents" / "leaky.md").write_text(planted, encoding="utf-8")
    r = collect(manifest, checkout, roots)
    assert len(r.refused_secrets) == 1
    assert r.refused_secrets[0].rel_path == "dotclaude/agents/leaky.md"
    assert not (checkout / "dotclaude" / "agents" / "leaky.md").exists()


def test_a1_denied_filename_never_copied(env):
    """Deny-matched live files are annotated (denied_live), never copied,
    and -- per the v0.2.1 contract change (R6) -- are the guard WORKING,
    not an alarm: no nonzero exit for their mere presence."""
    claude, _, checkout, manifest, roots = env
    (claude / "agents" / ".credentials.json").write_text("{}", encoding="utf-8")
    (claude / "agents" / "notes.secret").write_text("shh", encoding="utf-8")
    r = collect(manifest, checkout, roots)
    assert "agents/.credentials.json" in r.denied_live
    assert "agents/notes.secret" in r.denied_live  # manifest deny extends hard deny
    assert not (checkout / "dotclaude" / "agents" / ".credentials.json").exists()
    assert not (checkout / "dotclaude" / "agents" / "notes.secret").exists()


def test_collect_exclude_honored(env):
    claude, _, checkout, manifest, roots = env
    pyc = claude / "agents" / "__pycache__"
    pyc.mkdir()
    (pyc / "junk.cpython-312.pyc").write_bytes(b"\x00")
    r = collect(manifest, checkout, roots)
    assert r.copied == []
    assert not (checkout / "dotclaude" / "agents" / "__pycache__").exists()


def test_exclusion_is_symmetric_repo_side(env):
    """Excluded files IN THE CHECKOUT (e.g. hook-generated __pycache__) are
    invisible to sync -- not phantom apply-pending drift."""
    from dazzle_claude_config.syncmap import diff_all
    _, _, checkout, manifest, roots = env
    pyc = checkout / "dotclaude" / "agents" / "__pycache__"
    pyc.mkdir()
    (pyc / "junk.cpython-312.pyc").write_bytes(b"\x00")
    assert all(d.clean for d in diff_all(manifest, checkout, roots))


def test_missing_live_reported_not_deleted(env):
    claude, _, checkout, manifest, roots = env
    (claude / "agents" / "oracle.md").unlink()
    r = collect(manifest, checkout, roots)
    assert "dotclaude/agents/oracle.md" in r.missing_live
    assert (checkout / "dotclaude" / "agents" / "oracle.md").exists()


def test_a8_git_ignored_copy_detected(env, monkeypatch):
    """A newly collected file swallowed by a machine-level exclude is flagged.

    (Tracked files are immune to excludes -- git check-ignore consults the
    index first -- so A8 specifically protects NEW files, matching the
    Phase 0 incident where an unanchored 'CLAUDE.md' in .git/info/exclude
    silently dropped a first-time file.)
    """
    claude, _, checkout, manifest, roots = env
    monkeypatch.setattr("dazzle_claude_config.gitops.Path.home",
                        staticmethod(lambda: checkout.parent / "nonexistent-home"))
    (checkout / ".git" / "info").mkdir(exist_ok=True)
    (checkout / ".git" / "info" / "exclude").write_text("newbie.md\n",
                                                        encoding="utf-8")
    (claude / "agents" / "newbie.md").write_text("new agent\n", encoding="utf-8")
    repo = CheckoutRepo(checkout)
    r = collect(manifest, checkout, roots, repo=repo)
    assert "dotclaude/agents/newbie.md" in r.copied
    assert "dotclaude/agents/newbie.md" in r.git_ignored
