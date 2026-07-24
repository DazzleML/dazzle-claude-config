"""Edge cases discovered during the tester-unbounded Phase 1 exploratory run.

Original findings: tests/checklists/results/v0.1.x__Phase1__tester-unbounded-run-01.md
These began as characterization tests of the buggy behavior; after the fix
round they assert the CORRECTED behavior (per the "verbatim corrections
become test cases" convention).
"""
from __future__ import annotations

import os
import shutil
import stat
import sys

import pytest

from dazzle_claude_config.apply import apply
from dazzle_claude_config.cli import main
from dazzle_claude_config.collect import collect


@pytest.mark.skipif(sys.platform != "win32",
                    reason="chmod-to-read-only semantics exercised here are Windows-specific")
def test_apply_readonly_destination_recorded_not_raised(env, backup_dir):
    """FIXED (was HIGH): a read-only/locked destination no longer crashes
    apply() -- the failure is recorded per-file and the rest of the apply
    continues."""
    claude, _, checkout, manifest, roots = env
    (checkout / "dotclaude" / "CLAUDE.md").write_text("# v2 from repo\n", encoding="utf-8")
    (checkout / "dotclaude" / "agents" / "fresh.md").write_text("ok\n", encoding="utf-8")
    target = claude / "CLAUDE.md"
    os.chmod(target, stat.S_IREAD)
    try:
        r = apply(manifest, checkout, roots, backup_dir)
        assert any(path == "CLAUDE.md" for path, _ in r.failed)
        assert "agents/fresh.md" in r.copied  # other files still processed
        assert (claude / "agents" / "fresh.md").exists()
    finally:
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)


@pytest.mark.skipif(sys.platform != "win32",
                    reason="chmod-to-read-only semantics exercised here are Windows-specific")
def test_cli_apply_readonly_destination_exits_2(env, capsys):
    """FIXED (was HIGH): `ccs apply` against a read-only destination reports
    FAILED and returns exit 2 per the contract -- no traceback."""
    claude, user, checkout, _, _ = env
    (checkout / "dotclaude" / "CLAUDE.md").write_text("# v2 from repo\n", encoding="utf-8")
    target = claude / "CLAUDE.md"
    os.chmod(target, stat.S_IREAD)
    try:
        rc = main(["--checkout-dir", str(checkout), "--claude-dir", str(claude),
                   "--user-claude", str(user), "apply"])
        assert rc == 2
        assert "FAILED" in capsys.readouterr().out
    finally:
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)


def test_only_prefix_matching_zero_entries_warns(env, capsys):
    """FIXED (was LOW): --only with a prefix matching zero manifest entries
    now prints an explicit warning instead of a silent no-op."""
    claude, user, checkout, _, _ = env
    (checkout / "dotclaude" / "agents" / "new1.md").write_text("a\n", encoding="utf-8")
    rc = main(["--checkout-dir", str(checkout), "--claude-dir", str(claude),
               "--user-claude", str(user), "apply", "--only", "totally/bogus/prefix"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "matched no manifest entries" in out
    assert not (claude / "agents" / "new1.md").exists()


def test_secret_shaped_content_in_png_suffixed_file_refused(env):
    """FIXED (was MEDIUM): all suffixes are content-scanned -- a secret
    smuggled inside screenshot.png is refused, not collected."""
    claude, _, checkout, manifest, roots = env
    f = claude / "agents" / "screenshot.png"
    f.write_bytes(("token sk-ant-api03-" + "z" * 24 + "\n").encode("utf-8"))
    r = collect(manifest, checkout, roots)
    assert any(h.rel_path == "dotclaude/agents/screenshot.png"
               for h in r.refused_secrets)
    assert "dotclaude/agents/screenshot.png" not in r.copied
    assert not (checkout / "dotclaude" / "agents" / "screenshot.png").exists()


def test_real_binary_noise_not_false_positived(env):
    """Guard for the fix above: genuine binary bytes must not trip the scan."""
    claude, _, checkout, manifest, roots = env
    f = claude / "agents" / "icon.png"
    f.write_bytes(bytes(range(256)) * 64)
    r = collect(manifest, checkout, roots)
    assert "dotclaude/agents/icon.png" in r.copied
    assert r.refused_secrets == []


def test_type_mismatch_reported_and_skipped(env):
    """FIXED (was LOW): a directory target replaced by a plain file on the
    live side is reported as a mismatch and the entry is skipped -- no
    confusing nested artifact."""
    claude, _, checkout, manifest, roots = env
    shutil.rmtree(claude / "agents")
    (claude / "agents").write_text("oops, this is a FILE not a directory\n",
                                   encoding="utf-8")
    r = collect(manifest, checkout, roots)
    assert any("dotclaude/agents" in m and "type mismatch" in m
               for m in r.mismatched)
    assert not (checkout / "dotclaude" / "agents" / "agents").exists()
