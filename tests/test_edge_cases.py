"""Edge cases discovered during the tester-unbounded Phase 1 exploratory run.

Original findings: tests/checklists/results/v0.1.x__Phase1__tester-unbounded-run-01.md
These began as characterization tests of the buggy behavior; after the fix
round they assert the CORRECTED behavior (per the "verbatim corrections
become test cases" convention).

New findings below (tester-unbounded-run-02, v0.2.1 adversarial pass):
tests/checklists/results/v0.2.1__Feature__tester-unbounded-run-02.md -- began
as characterization tests of the buggy behavior; flipped to assert the
CORRECTED behavior in the same fix round.
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
from dazzle_claude_config.manifest import Manifest, ManifestError

from conftest import git


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


# --- v0.2.1 tester-unbounded-run-02 findings -------------------------------
#
# FIXED (was HIGH): apply() (checkout -> live) had NO deny-list awareness --
# the guard stack only protected collect()'s direction, so a denied-shaped
# file committed in the payload (accidental commit, bad merge, or a naive
# raw-~/.claude push via implicit mode) was copied straight into the live
# tree as an ordinary "applied:" line at exit 0. Now BOTH directions run
# is_denied(): apply refuses, reports REFUSED with the deny pattern, and
# exits 1 -- a denied file IN THE PAYLOAD is an anomaly to remove there,
# unlike collect's live-side denials (the guard working as intended, exit 0).

def test_apply_refuses_new_hard_denied_file_from_repo(env, backup_dir):
    """New (repo-only) HARD_DENY-named file in the checkout: apply() refuses
    it with the matching pattern; nothing reaches the live tree."""
    claude, _, checkout, manifest, roots = env
    (checkout / "dotclaude" / "agents" / ".credentials.json").write_text(
        '{"repo":"should never reach live silently"}\n', encoding="utf-8")
    r = apply(manifest, checkout, roots, backup_dir)
    assert ("dotclaude/agents/.credentials.json", ".credentials.json") \
        in r.refused_denied
    assert "agents/.credentials.json" not in r.copied
    assert not (claude / "agents" / ".credentials.json").exists()


def test_apply_refuses_to_overwrite_live_hard_denied_file(env, backup_dir):
    """Modified-path variant: the live side has a REAL file at a HARD_DENY
    name; the checkout has a DIFFERENT version. apply() must leave the live
    file untouched (the user's real secret survives, not as a backup but in
    place)."""
    claude, _, checkout, manifest, roots = env
    (claude / "agents" / ".credentials.json").write_text(
        '{"live":"the users real secret"}\n', encoding="utf-8")
    (checkout / "dotclaude" / "agents" / ".credentials.json").write_text(
        '{"repo":"stale or malicious payload content"}\n', encoding="utf-8")
    r = apply(manifest, checkout, roots, backup_dir)
    assert ("dotclaude/agents/.credentials.json", ".credentials.json") \
        in r.refused_denied
    assert "agents/.credentials.json" not in r.copied
    assert (claude / "agents" / ".credentials.json").read_text(encoding="utf-8") == \
        '{"live":"the users real secret"}\n'


def test_cli_apply_reports_refused_denied_and_exits_1(env, capsys):
    """CLI-level: `ccs apply` prints a REFUSED line naming the deny pattern
    and telling the user to remove the file from the payload repo, exit 1."""
    claude, user, checkout, _, _ = env
    (checkout / "dotclaude" / "agents" / ".credentials.json").write_text(
        "{}\n", encoding="utf-8")
    rc = main(["--checkout-dir", str(checkout), "--claude-dir", str(claude),
               "--user-claude", str(user), "apply"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "REFUSED (deny-list .credentials.json" in out
    assert "remove it from the payload repo" in out
    assert "applied: agents/.credentials.json" not in out
    assert not (claude / "agents" / ".credentials.json").exists()


# FIXED (was MEDIUM): Manifest.implicit()'s gate checked marker NAMES only,
# but entry synthesis requires the correct TYPE -- so a repo whose only
# marker was present with the WRONG type (e.g. a directory literally named
# CLAUDE.md) passed the gate yet synthesized ZERO entries, and every verb
# reported a misleadingly healthy "clean"/"nothing to do" at exit 0 with
# nothing actually tracked. Now zero synthesized entries raises
# ManifestError, matching HV.2's loud refusal for genuine non-config repos.

def test_implicit_manifest_wrong_marker_type_refused(tmp_path):
    """A directory named CLAUDE.md (not a file) passes the name gate but
    yields no usable entry -- implicit() must refuse, not synthesize an
    empty manifest."""
    co = tmp_path / "mirror"
    (co / "CLAUDE.md").mkdir(parents=True)  # directory, not a file
    (co / "CLAUDE.md" / "not_actually_the_memory_file.txt").write_text(
        "oops\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="expected type"):
        Manifest.implicit(co)


def test_cli_status_wrong_marker_type_exits_2(tmp_path, capsys):
    """CLI-level: status against such a repo errors loudly (exit 2, clear
    stderr message) instead of reporting a hollow 'status: clean'.

    Git-inits `co` (matching test_implicit.py's bare_mirror pattern) so
    CheckoutRepo() resolves to this repo itself rather than ambiently
    discovering an unrelated enclosing repo (e.g. a tracked home directory
    on the host running the suite) via `git rev-parse --show-toplevel`."""
    co = tmp_path / "mirror"
    (co / "CLAUDE.md").mkdir(parents=True)
    git(co, "init", "-q", "-b", "main")
    (co / ".keep").write_text("keep\n", encoding="utf-8")  # something to commit
    git(co, "add", "-A")
    git(co, "commit", "-q", "-m", "seed")
    claude = tmp_path / "claude"
    claude.mkdir()
    user = tmp_path / "user"
    user.mkdir()
    rc = main(["--checkout-dir", str(co), "--claude-dir", str(claude),
               "--user-claude", str(user), "status"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "status: clean" not in captured.out
    assert "expected type" in captured.err


def test_implicit_mode_nested_git_repo_invisible_to_sync(tmp_path):
    """Nested git repo inside a mirror (e.g. a skill cloned straight into
    skills/): its .git internals are excluded from sync in both directions
    -- never applied to live, never drift (tester run-02's organic repro
    for the apply gap copied 28 .git files verbatim into live)."""
    co = tmp_path / "mirror"
    (co / "skills" / "cloned").mkdir(parents=True)
    (co / "skills" / "cloned" / "SKILL.md").write_text("real\n", encoding="utf-8")
    (co / "skills" / "cloned" / ".git").mkdir()
    (co / "skills" / "cloned" / ".git" / "config").write_text(
        "[core]\n", encoding="utf-8")
    claude = tmp_path / "claude"
    claude.mkdir()
    roots = {"CLAUDE_DIR": claude, "USER_CLAUDE": tmp_path / "user"}
    m = Manifest.implicit(co)
    r = apply(m, co, roots, tmp_path / "backups")
    assert "skills/cloned/SKILL.md" in r.copied
    assert r.refused_denied == []
    assert (claude / "skills" / "cloned" / "SKILL.md").exists()
    assert not (claude / "skills" / "cloned" / ".git").exists()


def test_cli_nested_plain_dir_refused_a4_repo_root_guard(tmp_path, monkeypatch, capsys):
    """FAILING characterization test (not yet fixed): found during
    post-release PyPI validation of v0.2.1, HV.5 of the v0.1.0 checklist.

    gitops.py's CheckoutRepo -- and its own unit test,
    test_gitops.py::test_a4_nested_plain_dir_in_other_repo_refused, whose
    docstring reads "Guard (c) from the design contract: a plain (non-git)
    directory nested inside SOME OTHER (non-home) git repo must be refused
    -- toplevel != path -- so ccs never silently binds operations to the
    wrong repository root" -- both establish that this scenario must raise.
    It does -- but as GitopsSafetyError now, not GitError.

    But cli.py's _setup() (lines ~97-100) catches exactly that GitError
    and silently downgrades:

        try:
            repo = CheckoutRepo(checkout)
        except GitError:
            repo = None  # plain directory checkout: allowed, A8/A11 checks skipped

    So at the CLI layer -- the only layer an end user touches -- `ccs
    status`/`collect`/`apply` happily proceed against a --checkout-dir that
    is not its own git repository root, with ZERO warning printed that A8
    (git-ignore silent-drop detection) and A11 (merge-conflict detection)
    have both been silently disabled. The HV.5 checklist step ("Otherwise
    create a scratch repo, point --checkout-dir at a plain SUBDIR of it ...
    Expected: clean one-line error ... exit 2") fails against actual 0.2.1
    behavior: it returns exit 0, "status: clean", no error, no note.

    This is a genuine two-layer contract mismatch, not merely a stale
    checklist (contrast with HV.3's R6 rewording, where the *tests* also
    moved to the new contract) -- the gitops-level test still asserts
    "must be refused" while the CLI silently un-refuses it.
    """
    fake_home = tmp_path / "nonexistent-home"
    monkeypatch.setattr("dazzle_claude_config.gitops.Path.home",
                        staticmethod(lambda: fake_home))
    outer = tmp_path / "outer_repo"
    outer.mkdir()
    git(outer, "init", "-q", "-b", "main")
    (outer / "README.md").write_text("outer\n", encoding="utf-8")
    git(outer, "add", "-A")
    git(outer, "commit", "-q", "-m", "seed outer")
    nested_plain = outer / "some" / "nested" / "plain_dir"
    nested_plain.mkdir(parents=True)
    (nested_plain / "CLAUDE.md").write_text("# memory\n", encoding="utf-8")

    claude = tmp_path / "claude"
    claude.mkdir()
    user = tmp_path / "user"
    user.mkdir()

    rc = main(["--checkout-dir", str(nested_plain), "--claude-dir", str(claude),
               "--user-claude", str(user), "status"])
    captured = capsys.readouterr()

    # FIXED: gitops now raises GitopsSafetyError (a sibling of GitError that
    # _setup deliberately does not catch), so the refusal reaches the user.
    assert rc == 2, (
        f"expected exit 2 (refused per A4 'Guard (c)' contract), got {rc}. "
        f"stdout={captured.out!r} stderr={captured.err!r}")
    assert "not a git repository root" in captured.err
    assert "status: clean" not in captured.out
