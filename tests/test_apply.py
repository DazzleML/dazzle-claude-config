"""apply: A3 (no destruction without backup), A11 (conflict guard), A2 (idempotency)."""
import subprocess

import pytest

from dazzle_claude_config.apply import ApplyConflictError, apply
from dazzle_claude_config.collect import collect
from dazzle_claude_config.gitops import CheckoutRepo
from dazzle_claude_config.syncmap import diff_all

from conftest import GIT_ID, git


def test_clean_env_applies_nothing(env, backup_dir):
    _, _, checkout, manifest, roots = env
    r = apply(manifest, checkout, roots, backup_dir)
    assert r.copied == [] and r.backup_dir is None


def test_repo_change_applied_with_backup(env, backup_dir):
    claude, _, checkout, manifest, roots = env
    (checkout / "dotclaude" / "CLAUDE.md").write_text("# fleet v2\n", encoding="utf-8")
    r = apply(manifest, checkout, roots, backup_dir)
    assert "CLAUDE.md" in r.copied
    assert (claude / "CLAUDE.md").read_text(encoding="utf-8") == "# fleet v2\n"
    # A3: prior bytes recoverable
    assert r.backup_dir is not None
    assert (r.backup_dir / "CLAUDE.md").read_text(encoding="utf-8") == "# global memory v1\n"


def test_a3_removal_reported_by_default(env, backup_dir):
    claude, _, checkout, manifest, roots = env
    (claude / "agents" / "local-only.md").write_text("mine\n", encoding="utf-8")
    r = apply(manifest, checkout, roots, backup_dir)
    assert "agents/local-only.md" in r.removals_pending
    assert (claude / "agents" / "local-only.md").exists()  # untouched


def test_a3_sync_removals_stages_to_backup(env, backup_dir):
    claude, _, checkout, manifest, roots = env
    (claude / "agents" / "local-only.md").write_text("mine\n", encoding="utf-8")
    r = apply(manifest, checkout, roots, backup_dir, sync_removals=True)
    assert "agents/local-only.md" in r.removals_staged
    assert not (claude / "agents" / "local-only.md").exists()
    assert (r.backup_dir / "_removed" / "agents" / "local-only.md").read_text(
        encoding="utf-8") == "mine\n"


def test_seed_if_absent_seeds_once(env, backup_dir):
    claude, _, checkout, manifest, roots = env
    r = apply(manifest, checkout, roots, backup_dir)
    assert "settings.local.json" in r.seeded
    (claude / "settings.local.json").write_text('{"mine": true}', encoding="utf-8")
    r2 = apply(manifest, checkout, roots, backup_dir)
    assert r2.seeded == []
    assert (claude / "settings.local.json").read_text(encoding="utf-8") == '{"mine": true}'


def test_deferred_strategies_reported(env, backup_dir):
    _, _, checkout, manifest, roots = env
    r = apply(manifest, checkout, roots, backup_dir)
    assert any("render" in d for d in r.deferred)


def test_dry_run_changes_nothing(env, backup_dir):
    claude, _, checkout, manifest, roots = env
    (checkout / "dotclaude" / "CLAUDE.md").write_text("# fleet v2\n", encoding="utf-8")
    r = apply(manifest, checkout, roots, backup_dir, dry_run=True)
    assert "CLAUDE.md" in r.copied
    assert (claude / "CLAUDE.md").read_text(encoding="utf-8") == "# global memory v1\n"


def test_a11_conflicted_checkout_refused(env, backup_dir, monkeypatch):
    _, _, checkout, manifest, roots = env
    monkeypatch.setattr("dazzle_claude_config.gitops.Path.home",
                        staticmethod(lambda: checkout.parent / "nonexistent-home"))
    # Manufacture a real merge conflict in the checkout
    git(checkout, "checkout", "-q", "-b", "side")
    (checkout / "dotclaude" / "CLAUDE.md").write_text("side\n", encoding="utf-8")
    git(checkout, "commit", "-qam", "side edit")
    git(checkout, "checkout", "-q", "main")
    (checkout / "dotclaude" / "CLAUDE.md").write_text("main\n", encoding="utf-8")
    git(checkout, "commit", "-qam", "main edit")
    # Must carry the same identity conftest's git() helper injects: a bare
    # `git merge` aborts with "Committer identity unknown" (rc 128) on any
    # machine without a global git identity -- every CI runner. That satisfies
    # "returncode != 0" while creating no conflict at all, so the old
    # assertion passed for the wrong reason and the NEXT line failed in CI.
    result = subprocess.run(["git", *GIT_ID, "merge", "side"], cwd=str(checkout),
                            capture_output=True, text=True)
    assert result.returncode != 0, "merge unexpectedly succeeded -- no conflict to test"
    assert "CONFLICT" in result.stdout + result.stderr, (
        "merge failed for a non-conflict reason:\n"
        f"{result.stdout}\n{result.stderr}")
    repo = CheckoutRepo(checkout)
    assert repo.has_conflicts()
    with pytest.raises(ApplyConflictError):
        apply(manifest, checkout, roots, backup_dir, repo=repo)


def test_a2_collect_apply_round_trip_idempotent(env, backup_dir):
    claude, _, checkout, manifest, roots = env
    (claude / "agents" / "newbie.md").write_text("new agent\n", encoding="utf-8")
    (claude / "CLAUDE.md").write_text("# v2\n", encoding="utf-8")
    collect(manifest, checkout, roots)
    r = apply(manifest, checkout, roots, backup_dir)
    assert r.copied == []  # apply after collect: nothing to do
    assert all(d.clean for d in diff_all(manifest, checkout, roots))
    r2 = collect(manifest, checkout, roots)
    assert r2.copied == []  # second collect: byte-stable
