"""Repro (FIXED same run -- the dry-run wording now says would seed /
would reseed; the former strict-xfail tests below now pass as plain tests): `ccs apply --dry-run` prints "seeded ... was absent locally" and
"reseeded ... previous copy backed up; the fresh seed is live" with NO
dry-run qualifier, even though nothing was written to disk -- unlike the
ordinary one-to-one copy path, which correctly says "would apply" instead
of "applied" under --dry-run.

Found during the v0.5.2 human-checklist run (tester-unbounded), while
verifying Section 3.4 ("--dry-run: reports, writes nothing") and its
edge-case extension. The FILESYSTEM behavior is correct in both cases
(nothing is written) -- this is purely a message-wording gap, but it is
exactly the kind of overclaiming this wave's own #27 slice ("status stops
overclaiming about seeded files") and the doctor contract ("a doctor that
writes or flatters is worse than none" -- HV.1's rationale in
tests/checklists/v0.5.2__Feature__setup-doctor-seed-decisions-directory-seeds.md)
were built to eliminate elsewhere in this same release.

Root cause: dazzle_claude_config/cli.py, in the `apply` verb's result
rendering (~line 1621 onward):

    for rel in r.copied:
        verb = "would apply" if args.dry_run else "applied"      # line 1635
        print(f"{c('green', verb)}: {rel}")
    for rel in r.reseeded:
        print(f"{c('cyan', 'reseeded')} {rel} "                  # line 1637-1639
              f"{c('dim', '-- previous copy backed up; the fresh seed is live')}")
    for rel in r.seeded:
        print(f"{c('green', 'seeded')} {rel} {c('dim', '-- was absent locally')}")  # 1640-1641

The `r.copied` loop branches on `args.dry_run` to pick "would apply" vs
"applied" -- the correct pattern, already present in this exact function.
The `r.reseeded` and `r.seeded` loops do NOT branch on `args.dry_run` at
all: the text is identical whether or not `--dry-run` was passed, and the
wording is unconditionally past/present-completed ("was absent locally"
implies it no longer is; "the fresh seed is live" asserts current fact).
`dazzle_claude_config/apply.py` populates `result.seeded` /
`result.reseeded` the same way regardless of `dry_run` (the write itself
is correctly gated by `if not dry_run:` at apply.py lines ~150, 159, 190,
199 -- only the message text ignores the flag), so a human running
`ccs apply --dry-run` to preview a migration before committing to it sees
line-for-line the same seed/reseed messages as a real run, with nothing
in the sentence itself distinguishing preview from action. The `backups:
<dir>` summary line (cli.py ~1657-1658) makes it worse for --reseed:
under a real run a directory now exists there; under --dry-run the same
line prints, naming a backup directory that was never created.

Two reasonable fixes for the maintainer to choose between (not applied
here, diagnose-not-fix):
  (a) Mirror the `r.copied` pattern: `verb = "would seed" if args.dry_run
      else "seeded"` / `"would reseed" if args.dry_run else "reseeded"`,
      and suffix the tail clause similarly ("the fresh seed would be
      live" under dry-run).
  (b) Print a single "(dry run -- nothing written)" trailer once at the
      end of the whole apply report when args.dry_run is set, rather
      than threading the distinction through every message individually.

This is a DIAGNOSIS-ONLY repro (tester-unbounded: diagnose and report,
not fix). Not named test_*.py so it is not swept into the default
`pytest -q` collection (testpaths=["tests"], python_files="test_*.py")
and does not change the regression baseline count; run explicitly:

    pytest tests/one-offs/repro_dry_run_seed_reseed_overclaims.py -v
"""
from __future__ import annotations

import json
import subprocess as sp
from pathlib import Path

import pytest

from dazzle_claude_config.cli import main

GIT_ID = ["-c", "user.email=repro@test.invalid", "-c", "user.name=repro",
          "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]


def _git(cwd: Path, *args: str) -> None:
    r = sp.run(["git", *GIT_ID, "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"


@pytest.fixture
def world(tmp_path):
    co = tmp_path / "co"
    sp.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    (co / "dotclaude").mkdir()
    (co / "dotclaude" / "CLAUDE.md").write_text("seed content\n", encoding="utf-8")
    manifest = {"manifest_version": 1,
                "territories": {"dotclaude": {"root_var": "CLAUDE_DIR", "repo_dir": "dotclaude"}},
                "entries": [{"repo": "dotclaude/CLAUDE.md", "territory": "dotclaude",
                             "target": "CLAUDE.md", "strategy": "seed-if-absent"}]}
    (co / "ccs-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _git(co, "add", "-A")
    _git(co, "commit", "-qm", "seed")
    live = tmp_path / "live"; live.mkdir()
    user = tmp_path / "user"; user.mkdir()
    return dict(co=co, live=live, user=user)


def _run(w, *extra) -> int:
    return main(["--checkout-dir", str(w["co"]), "--claude-dir", str(w["live"]),
                 "--user-claude", str(w["user"]), "--no-color", "--no-fetch",
                 "apply", *extra])


def test_dry_run_seed_writes_nothing_to_disk(world):
    """Control: the FILESYSTEM contract is upheld -- this passes today and
    should keep passing under any fix to the wording (below)."""
    rc = _run(world, "--dry-run")
    assert rc == 0
    assert not (world["live"] / "CLAUDE.md").exists(), (
        "dry-run must never write the seed file -- if this fails, the bug "
        "is far worse than message wording"
    )


def test_dry_run_seed_message_says_would_not_was(world, capsys):
    rc = _run(world, "--dry-run")
    out = capsys.readouterr().out
    assert rc == 0
    # Desired: something in the line marks it as a preview, e.g. "would
    # seed" or a trailing "(dry run)" -- today it reads exactly like a
    # completed action: "seeded CLAUDE.md -- was absent locally".
    assert "would" in out.lower() or "dry" in out.lower(), (
        f"dry-run seed message does not distinguish itself from a real "
        f"seed delivery: {out!r}"
    )


def test_dry_run_reseed_leaves_live_file_untouched(world):
    """Control: filesystem contract again -- must keep passing."""
    _run(world)  # real apply: seeds CLAUDE.md for real
    (world["live"] / "CLAUDE.md").write_text("user edited\n", encoding="utf-8")
    rc = _run(world, "--reseed", "CLAUDE.md", "--dry-run")
    assert rc == 0
    assert (world["live"] / "CLAUDE.md").read_text(encoding="utf-8") == "user edited\n", (
        "dry-run --reseed must never touch the live file"
    )


def test_dry_run_reseed_message_says_would_not_is_live(world, capsys):
    _run(world)
    (world["live"] / "CLAUDE.md").write_text("user edited\n", encoding="utf-8")
    rc = _run(world, "--reseed", "CLAUDE.md", "--dry-run")
    out = capsys.readouterr().out
    assert rc == 0
    assert "would" in out.lower() or "dry" in out.lower(), (
        f"dry-run reseed message does not distinguish itself from a real "
        f"reseed: {out!r}"
    )
    assert "backups:" not in out, (
        f"dry-run --reseed reports a backups: <dir> line naming a "
        f"directory that --dry-run never created: {out!r}"
    )


def test_real_copy_path_already_gets_this_right(world, capsys):
    """Control: the ORDINARY one-to-one copy path (r.copied) already
    branches correctly on args.dry_run (cli.py line 1635) -- proves the
    fix pattern already exists in this file, just not applied to the
    seed/reseed loops a few lines below it."""
    # Add a plain `copy` entry alongside the seed-if-absent one so r.copied
    # is nonempty.
    manifest_path = world["co"] / "ccs-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["entries"].append({"repo": "dotclaude/PLAIN.md", "territory": "dotclaude",
                                 "target": "PLAIN.md", "strategy": "copy"})
    (world["co"] / "dotclaude" / "PLAIN.md").write_text("plain\n", encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _git(world["co"], "add", "-A")
    _git(world["co"], "commit", "-qm", "add plain copy entry")

    rc = _run(world, "--dry-run")
    out = capsys.readouterr().out
    assert rc == 0
    assert "would apply: PLAIN.md" in out
    assert not (world["live"] / "PLAIN.md").exists()
