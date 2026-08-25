"""Guided migration and the seed walk (#26 slice 4, #27's front end).

The migration exists because the four beats -- keep a copy, take the new
starter, prove both copies hold the original bytes, verify the result --
were run by hand on two real machines, and on the second one the PROOF
step was silently skipped (a placeholder path was left in the compare
command; it failed to find the file and nobody noticed). A step a human
skips is a step the tool should perform.

The walk exists because per-file commands are tedious exactly when there
are several to answer, which is precisely after a payload restructure.
"""
from __future__ import annotations

import json
import subprocess as sp
from pathlib import Path

import pytest

from dazzle_claude_config import migrate, seeddecisions
from dazzle_claude_config.cli import main

from conftest import GIT_ID

OLD = b"# the old starter\nrule one\nrule two\n"
NEW = b"# the new stub\n@import layers\n"
MINE = b"# my own thing\n"

MANIFEST = {
    "manifest_version": 1,
    "territories": {"dotclaude": {"root_var": "CLAUDE_DIR", "repo_dir": "dotclaude"}},
    "entries": [
        {"repo": "dotclaude/CLAUDE.md", "territory": "dotclaude",
         "target": "CLAUDE.md", "strategy": "seed-if-absent"},
        {"repo": "dotclaude/NOTES.md", "territory": "dotclaude",
         "target": "NOTES.md", "strategy": "seed-if-absent"},
    ],
}


def _git(cwd: Path, *args: str) -> str:
    r = sp.run(["git", *GIT_ID, "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout


@pytest.fixture
def world(tmp_path):
    co = tmp_path / "co"
    sp.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    (co / "dotclaude").mkdir()
    (co / "ccs-manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (co / "dotclaude" / "CLAUDE.md").write_bytes(OLD)
    (co / "dotclaude" / "NOTES.md").write_bytes(b"# notes starter\n")
    _git(co, "add", "-A"); _git(co, "commit", "-qm", "starters v1")
    (co / "dotclaude" / "CLAUDE.md").write_bytes(NEW)
    _git(co, "commit", "-qam", "the restructure")
    live = tmp_path / "live"; live.mkdir()
    user = tmp_path / "user"; user.mkdir()
    (live / "CLAUDE.md").write_bytes(OLD)          # the real migration case
    return dict(co=co, live=live, user=user, tmp=tmp_path)


def _ccs(w, *verb) -> list[str]:
    return ["--checkout-dir", str(w["co"]), "--claude-dir", str(w["live"]),
            "--user-claude", str(w["user"]), "--no-color", "--no-fetch", *verb]


def _backup_root(w) -> Path:
    return w["user"] / "backups" / "ccs"


# -- the migration, end to end ------------------------------------------------

def test_migration_keeps_a_copy_takes_the_new_one_and_proves_it(world, capsys, monkeypatch):
    rc = main(_ccs(world, "migrate", "CLAUDE.md"))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert (world["live"] / "CLAUDE.md").read_bytes() == NEW      # took the new
    assert "your pre-migration copy is intact" in out
    assert "byte for byte" in out                                  # ccs's backup too
    assert "the payload's version is live now" in out
    kept = list((world["user"] / "backups" / "pre-migrate").glob("CLAUDE.md.*"))
    assert kept and kept[0].read_bytes() == OLD                    # the keep-copy


def test_backup_root_honours_the_user_claude_override(world, capsys):
    # Regression (v0.5.4 checklist run-01): _run_migration derived its backup
    # root from the real home directory instead of THIS RUN's territories, so
    # a scratch run wrote into the operator's actual ~/claude/backups. Both
    # copies must land under the --user-claude tree and nowhere else.
    rc = main(_ccs(world, "migrate", "CLAUDE.md"))
    assert rc == 0
    kept = list((world["user"] / "backups" / "pre-migrate").glob("CLAUDE.md.*"))
    assert kept and kept[0].read_bytes() == OLD
    ccs_backups = list((world["user"] / "backups" / "ccs").glob("*__apply"))
    assert ccs_backups, "apply's own backup dir must be under the scratch user tree too"
    assert (ccs_backups[0] / "CLAUDE.md").read_bytes() == OLD


def test_migration_dry_run_writes_nothing(world, capsys, monkeypatch):
    rc = main(_ccs(world, "migrate", "CLAUDE.md", "--dry-run"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "would keep a copy" in out and "nothing was written" in out
    assert (world["live"] / "CLAUDE.md").read_bytes() == OLD       # untouched


def test_migration_refuses_a_file_this_box_does_not_have(world, capsys):
    rc = main(_ccs(world, "migrate", "NOTES.md"))
    err = capsys.readouterr().err
    assert rc == 2
    assert "does not exist on this box yet" in err


def test_migration_refuses_a_non_seed_target(world, capsys):
    rc = main(_ccs(world, "migrate", "not-a-thing.md"))
    assert rc == 2
    assert "is not a seed entry" in capsys.readouterr().err


def test_bare_migrate_lists_candidates(world, capsys):
    rc = main(_ccs(world, "migrate"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "can be migrated" in out and "CLAUDE.md" in out
    assert "never edited" in out            # the untouched-old reason


def test_bare_migrate_says_nothing_to_do_when_clean(world, capsys):
    (world["live"] / "CLAUDE.md").write_bytes(NEW)
    rc = main(_ccs(world, "migrate"))
    assert rc == 0
    assert "nothing to migrate" in capsys.readouterr().out


def test_verification_catches_a_corrupted_backup(world, monkeypatch, capsys):
    # The proof must be able to FAIL: if ccs's own backup does not hold the
    # pre-migration bytes, the migration reports a PROBLEM rather than
    # congratulating itself.
    real_save = None
    from dazzle_claude_config import backup as _backup

    def sabotage(self, src, rel):
        dest = real_save(self, src, rel)
        Path(dest).write_bytes(b"corrupted\n")
        return dest
    real_save = _backup.BackupSession.save
    monkeypatch.setattr(_backup.BackupSession, "save", sabotage)
    rc = main(_ccs(world, "migrate", "CLAUDE.md"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "does NOT match the pre-migration bytes" in out
    assert "keep your copy" in out


# -- the proof must be able to fail (mutation-sweep closures) ---------------

def test_verification_catches_a_corrupted_keep_copy(world, monkeypatch, capsys):
    # M08: the kept copy is only "intact" if its BYTES match -- existence is
    # not proof. Sabotage the copy itself.
    import shutil as _sh
    from dazzle_claude_config import migrate as _m

    def bad_copy(src, dst, *a, **k):
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_bytes(b"truncated")
        return dst
    monkeypatch.setattr(_m.shutil, "copy2", bad_copy)
    rc = main(_ccs(world, "migrate", "CLAUDE.md"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "does not match the bytes that were live" in out


def test_missing_backup_dir_is_a_problem_not_a_pass(world, monkeypatch, capsys):
    # M09: if apply reports no backup directory, the migration has lost half
    # its evidence -- that is a PROBLEM, never a verified line.
    from dazzle_claude_config import migrate as _m
    real = _m.apply

    def no_backup_dir(*a, **k):
        r = real(*a, **k)
        r.backup_dir = None
        return r
    monkeypatch.setattr(_m, "apply", no_backup_dir)
    rc = main(_ccs(world, "migrate", "CLAUDE.md"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "PROBLEM" in out and "no backup directory" in out


def test_failed_reseed_reports_applys_own_reason(world, monkeypatch, capsys):
    # M07: the reason comes from the RESOLVED entry's failure row, so naming
    # the entry by its repo path still surfaces apply's message.
    from dazzle_claude_config import migrate as _m
    from dazzle_claude_config.apply import ApplyResult

    def failing(*a, **k):
        r = ApplyResult()
        r.failed.append(("CLAUDE.md", "disk on fire"))
        return r
    monkeypatch.setattr(_m, "apply", failing)
    rc = main(_ccs(world, "migrate", "dotclaude/CLAUDE.md"))   # repo path
    out = capsys.readouterr().out
    assert rc == 1
    assert "disk on fire" in out


def test_keep_root_empty_falls_back_beside_the_backups(world, monkeypatch):
    # M05: an empty keep_root must NOT resolve to the working directory.
    from dazzle_claude_config import migrate as _m
    from dazzle_claude_config.manifest import Manifest
    from dazzle_claude_config.platform_info import territory_roots
    m = Manifest.load(world["co"])
    roots = territory_roots(str(world["live"]), str(world["user"]))
    r = _m.reseed_migration(m, world["co"], roots, _backup_root(world),
                            "CLAUDE.md", keep_root="", dry_run=True)
    assert r.keep_copy.parent == _backup_root(world).parent / "pre-migrate"


def test_reopened_files_are_migration_candidates(world, capsys):
    # M02: a file whose keep-decision was overtaken by a new starter is
    # exactly a migration candidate -- it must not fall out of the list.
    (world["live"] / "CLAUDE.md").write_bytes(MINE)
    seeddecisions.keep("CLAUDE.md", "until-changed", "stale-hash",
                       world["user"])
    rc = main(_ccs(world, "migrate"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "CLAUDE.md" in out and "both moved" in out


# -- the walk -----------------------------------------------------------------

def _answers(monkeypatch, *keys):
    it = iter(keys)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_a: next(it))


def test_walk_keeps_mine_with_one_keystroke(world, capsys, monkeypatch):
    (world["live"] / "CLAUDE.md").write_bytes(MINE)
    _answers(monkeypatch, "k")
    rc = main(_ccs(world, "seed"))
    out = capsys.readouterr().out
    assert rc == 0 and "kept yours (until-changed)" in out
    assert seeddecisions.load(world["user"]).by_target["CLAUDE.md"]["mode"] \
        == "until-changed"


def test_walk_always_records_always(world, capsys, monkeypatch):
    (world["live"] / "CLAUDE.md").write_bytes(MINE)
    _answers(monkeypatch, "a")
    main(_ccs(world, "seed"))
    assert seeddecisions.load(world["user"]).by_target["CLAUDE.md"]["mode"] == "always"


def test_walk_take_runs_the_verified_migration(world, capsys, monkeypatch):
    _answers(monkeypatch, "t")
    rc = main(_ccs(world, "seed"))
    out = capsys.readouterr().out
    assert rc == 0
    assert (world["live"] / "CLAUDE.md").read_bytes() == NEW
    assert "your pre-migration copy is intact" in out     # proof, inside the walk


def test_walk_diff_reasks_the_same_file(world, capsys, monkeypatch):
    (world["live"] / "CLAUDE.md").write_bytes(MINE)
    launched = []
    monkeypatch.setattr("dazzle_claude_config.merge.resolve_difftool",
                        lambda t=None: "faketool")
    monkeypatch.setattr("dazzle_claude_config.merge.launch_difftool",
                        lambda name, l, r: launched.append((name, l, r)) or 0)
    _answers(monkeypatch, "d", "k")          # look, THEN decide
    main(_ccs(world, "seed"))
    out = capsys.readouterr().out
    assert len(launched) == 1                 # the tool opened once
    assert out.count("(1/1) CLAUDE.md") == 2  # and the same file was asked twice
    assert "kept yours" in out


def test_walk_skip_and_quit_decide_nothing(world, capsys, monkeypatch):
    (world["live"] / "CLAUDE.md").write_bytes(MINE)
    _answers(monkeypatch, "s")
    main(_ccs(world, "seed"))
    assert seeddecisions.load(world["user"]).by_target == {}
    _answers(monkeypatch, "q")
    main(_ccs(world, "seed"))
    assert seeddecisions.load(world["user"]).by_target == {}


def test_walk_rejects_gibberish_then_accepts(world, capsys, monkeypatch):
    (world["live"] / "CLAUDE.md").write_bytes(MINE)
    _answers(monkeypatch, "x", "", "k")
    main(_ccs(world, "seed"))
    out = capsys.readouterr().out
    assert out.count("please answer k, a, t, d, s, or q") == 2
    assert "kept yours" in out


def test_walk_is_a_no_op_when_nothing_is_open(world, capsys, monkeypatch):
    (world["live"] / "CLAUDE.md").write_bytes(NEW)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    rc = main(_ccs(world, "seed"))
    assert rc == 0 and "nothing to decide" in capsys.readouterr().out


def test_walk_refuses_to_prompt_when_piped(world, capsys, monkeypatch):
    (world["live"] / "CLAUDE.md").write_bytes(MINE)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    rc = main(_ccs(world, "seed"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "run `ccs seed` from a terminal" in out


# -- seed diff ----------------------------------------------------------------

def test_seed_diff_opens_yours_against_the_payloads(world, capsys, monkeypatch):
    (world["live"] / "CLAUDE.md").write_bytes(MINE)
    calls = []
    monkeypatch.setattr("dazzle_claude_config.merge.resolve_difftool",
                        lambda t=None: "faketool")
    monkeypatch.setattr("dazzle_claude_config.merge.launch_difftool",
                        lambda name, l, r: calls.append((name, l, r)) or 0)
    rc = main(_ccs(world, "seed", "diff", "CLAUDE.md"))
    assert rc == 0 and len(calls) == 1
    name, left, right = calls[0]
    assert left.read_bytes() == NEW and right.read_bytes() == MINE   # payload | yours
    assert "opened faketool" in capsys.readouterr().out


def test_walk_does_not_count_a_failed_migration_as_decided(world, capsys, monkeypatch):
    # M12: only a migration that actually succeeded counts.
    from dazzle_claude_config import migrate as _m

    def bad_copy(src, dst, *a, **k):
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_bytes(b"truncated")
        return dst
    monkeypatch.setattr(_m.shutil, "copy2", bad_copy)
    _answers(monkeypatch, "t")
    main(_ccs(world, "seed"))
    out = capsys.readouterr().out
    assert "done -- 0 of 1 decided" in out


def test_walk_quit_counts_the_current_file_as_remaining(world, capsys, monkeypatch):
    # M13: quitting on the first of two leaves TWO undecided, not one.
    (world["live"] / "CLAUDE.md").write_bytes(MINE)
    (world["live"] / "NOTES.md").write_bytes(b"# mine too\n")
    _answers(monkeypatch, "q")
    main(_ccs(world, "seed"))
    assert "0 decided, 2 left" in capsys.readouterr().out
