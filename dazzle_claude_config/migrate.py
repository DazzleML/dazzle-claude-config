"""Guided migrations: keep a copy, take the payload's version, prove it (#26).

A payload can replace a starter file a box already owns -- the layered
CLAUDE.md restructure did exactly that to two real machines. The move is
always the same four beats: copy the live file somewhere safe, take the
new starter, prove the copy holds the original bytes, and verify the
result. Both real migrations ran those beats by hand from a runbook, and
the proof step is the one a human silently skips (measured: on the second
box the byte-compare was typed with a placeholder path still in it, failed
to find the file, and was never completed -- the migration was fine, but
nobody had checked).

This module performs them, so the proof is not optional:

    pre-hash the live file
      -> keep-copy it OUTSIDE the apply backup tree (a plain copy2, a
         different code path from BackupSession, so a bug in one does not
         silently corrupt both)
      -> apply --reseed (which makes its own backup)
      -> verify BOTH copies hash to the pre-migration bytes

What that proves: the bytes that existed before the migration still exist,
twice, in two places written by two different code paths. What it cannot
prove: correctness of a tool by the tool's own testimony alone -- an
operator who wants an outside witness should still make their own copy
first, which this never prevents (the keep-copy is additive).
"""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .apply import apply
from .manifest import Manifest
from .syncmap import entry_applies, entry_bases


class MigrateError(RuntimeError):
    pass


@dataclass
class MigrationResult:
    target: str = ""
    pre_sha: str = ""
    pre_size: int = 0
    keep_copy: Path | None = None
    ccs_backup: Path | None = None
    verified: list[str] = field(default_factory=list)   # human-readable proofs
    problems: list[str] = field(default_factory=list)
    reseeded: bool = False
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return not self.problems


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_seed_entry(manifest: Manifest, target: str, box_tags=frozenset()):
    """The seed entry a migration names, by target or repo path."""
    want = target.replace(chr(92), "/").strip("/")
    for entry in manifest.seed_entries():
        if want in (entry.target, entry.repo) and entry_applies(entry, box_tags):
            return entry
    return None


def candidates(findings) -> list[tuple[str, str]]:
    """[(target, state)] worth migrating, from _seed_findings' output."""
    return [(t, s) for t, s, *_ in findings
            if s in ("untouched-old", "open", "reopened")]


def reseed_migration(manifest: Manifest, checkout: Path, roots: dict,
                     backup_root: Path, target: str, *, repo=None,
                     box_tags=frozenset(), dry_run: bool = False,
                     keep_root: Path | None = None) -> MigrationResult:
    """Run (or preview) the four beats for one seed target."""
    result = MigrationResult(target=target, dry_run=dry_run)
    entry = find_seed_entry(manifest, target, box_tags)
    if entry is None:
        raise MigrateError(
            f"{target!r} is not a seed entry on this box "
            "(`ccs seed list` shows the ones that are)")
    live, seed = entry_bases(entry, checkout, roots, manifest.territories)
    if not seed.is_file():
        raise MigrateError(f"the payload has no starter file for {entry.target} "
                           f"({seed}) -- nothing to migrate to")
    if not live.is_file():
        raise MigrateError(
            f"{entry.target} does not exist on this box yet -- a plain "
            "`ccs apply` seeds it; migration is for replacing a file you have")
    result.target = entry.target
    result.pre_sha = sha256_of(live)
    result.pre_size = live.stat().st_size

    stamp = datetime.now().strftime("%Y-%m-%d__%H-%M-%S")
    keep_dir = Path(keep_root) if keep_root else Path(backup_root).parent / "pre-migrate"
    keep = keep_dir / f"{entry.target.replace('/', '__')}.{stamp}"
    result.keep_copy = keep
    if dry_run:
        result.verified.append(
            f"would keep a copy of {entry.target} ({result.pre_size} bytes) at {keep}")
        result.verified.append(
            "would take the payload's starter, then verify both copies "
            "hash to the pre-migration bytes")
        return result

    keep_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(live, keep)          # deliberately NOT BackupSession

    r = apply(manifest, checkout, roots, backup_root, repo=repo,
              dry_run=False, box_tags=box_tags, reseed=entry.target)
    result.reseeded = entry.target in r.reseeded
    if not result.reseeded:
        why = next((f"{p}: {e}" for p, e in r.failed if p == entry.target),
                   "apply did not report the reseed")
        result.problems.append(f"the reseed did not happen ({why})")
        return result
    if r.backup_dir:
        result.ccs_backup = Path(r.backup_dir) / entry.target

    # THE PROOF. Both copies must hold the pre-migration bytes, and the live
    # file must now hold the payload's.
    if keep.is_file() and sha256_of(keep) == result.pre_sha:
        result.verified.append(f"your pre-migration copy is intact at {keep}")
    else:
        result.problems.append(f"the kept copy at {keep} does not match the "
                               "bytes that were live before the migration")
    if result.ccs_backup is None:
        result.problems.append("apply reported no backup directory")
    elif result.ccs_backup.is_file() and sha256_of(result.ccs_backup) == result.pre_sha:
        result.verified.append(f"ccs's own backup matches it, byte for byte "
                               f"({result.ccs_backup})")
    else:
        result.problems.append(
            f"ccs's backup at {result.ccs_backup} does NOT match the "
            "pre-migration bytes -- keep your copy and investigate")
    if live.is_file() and sha256_of(live) == sha256_of(seed):
        result.verified.append("the payload's version is live now")
    else:
        result.problems.append("the live file does not match the payload's "
                               "starter after the reseed")
    return result
