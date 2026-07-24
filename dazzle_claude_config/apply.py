"""apply: payload checkout -> live tree, never destructively.

Every overwrite is preceded by a backup copy (A3); removals are never
performed in place -- with --sync-removals they are MOVED into the backup
dir (staged removal), otherwise only reported. Refuses to run while the
checkout has unresolved merge conflicts (A11). Deferred strategies
(render, plugins -- Phase 2) are reported, not silently dropped.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .backup import BackupSession
from .gitops import CheckoutRepo
from .manifest import Manifest
from .syncmap import diff_all, entry_applies, entry_bases


class ApplyConflictError(RuntimeError):
    """The checkout (merge arena) has unresolved conflicts."""


@dataclass
class ApplyResult:
    copied: list[str] = field(default_factory=list)
    seeded: list[str] = field(default_factory=list)
    removals_pending: list[str] = field(default_factory=list)  # reported, not synced
    removals_staged: list[str] = field(default_factory=list)   # moved to backup
    deferred: list[str] = field(default_factory=list)          # render/plugins entries
    failed: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)
    mismatched: list[str] = field(default_factory=list)        # type-conflict entries
    only_matched: int = 0                                      # entries passing --only
    backup_dir: Path | None = None


def apply(manifest: Manifest, checkout: Path, roots: dict[str, Path],
          backup_root: Path, repo: CheckoutRepo | None = None,
          dry_run: bool = False, only: str | None = None,
          sync_removals: bool = False) -> ApplyResult:
    if repo is not None and repo.has_conflicts():
        raise ApplyConflictError(
            "checkout has unresolved merge conflicts; resolve them "
            "(normal git tools) before apply")

    result = ApplyResult()
    session = BackupSession(backup_root)

    for d in diff_all(manifest, checkout, roots):
        if only and not d.entry.repo.startswith(only):
            continue
        result.only_matched += 1
        if d.mismatch:
            result.mismatched.append(f"{d.entry.repo}: {d.mismatch}")
            continue
        # repo -> live: new files and modified files
        for rel in d.repo_only + d.modified:
            src = d.repo_base / rel if rel else d.repo_base
            dest = d.live_base / rel if rel else d.live_base
            display = f"{d.entry.target}/{rel}" if rel else d.entry.target
            if rel in d.repo_only and not src.exists():
                continue
            if not dry_run:
                try:
                    if dest.exists():
                        session.save(dest, display)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                except OSError as e:
                    # A locked or read-only destination (common on Windows)
                    # must not abort the whole apply -- record and continue.
                    result.failed.append((display, str(e)))
                    continue
            result.copied.append(display)
        # live-only files under a manifest-owned target: removal candidates
        for rel in d.live_only:
            display = f"{d.entry.target}/{rel}" if rel else d.entry.target
            if sync_removals:
                if not dry_run:
                    try:
                        session.stage_removal(
                            d.live_base / rel if rel else d.live_base, display)
                    except OSError as e:
                        result.failed.append((display, str(e)))
                        continue
                result.removals_staged.append(display)
            else:
                result.removals_pending.append(display)

    for entry in manifest.seed_entries():
        if not entry_applies(entry):
            continue
        if only and not entry.repo.startswith(only):
            continue
        result.only_matched += 1
        live_base, repo_base = entry_bases(
            entry, checkout, roots, manifest.territories)
        if repo_base.is_file() and not live_base.exists():
            if not dry_run:
                try:
                    live_base.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(repo_base, live_base)
                except OSError as e:
                    result.failed.append((entry.target, str(e)))
                    continue
            result.seeded.append(entry.target)

    result.deferred = [f"{e.repo} ({e.strategy})" for e in manifest.deferred_entries()]
    if session.saved:
        result.backup_dir = session.dir
    return result
