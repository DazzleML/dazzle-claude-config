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
from .secrets import is_denied
from .syncmap import diff_all, entry_applies, entry_bases


class ApplyConflictError(RuntimeError):
    """The checkout (merge arena) has unresolved conflicts."""


@dataclass
class ApplyResult:
    copied: list[str] = field(default_factory=list)
    seeded: list[str] = field(default_factory=list)
    refused_denied: list[tuple[str, str]] = field(default_factory=list)  # (repo rel, pattern)
    removals_pending: list[str] = field(default_factory=list)  # reported, not synced
    local_only: list[str] = field(default_factory=list)        # never in the checkout
    removals_staged: list[str] = field(default_factory=list)   # moved to backup
    deferred: list[str] = field(default_factory=list)          # render/plugins entries
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (path, reason): wrong direction
    failed: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)
    mismatched: list[str] = field(default_factory=list)        # type-conflict entries
    only_matched: int = 0                                      # entries passing --only
    backup_dir: Path | None = None


def apply(manifest: Manifest, checkout: Path, roots: dict[str, Path],
          backup_root: Path, repo: CheckoutRepo | None = None,
          dry_run: bool = False, only: str | None = None,
          sync_removals: bool = False,
          skip: dict[str, str] | None = None,
          box_tags=frozenset()) -> ApplyResult:
    """`skip` maps a target path (`skills/x.md`) to a reason. Files whose LIVE
    copy is ahead of the checkout have nothing to apply -- copying would revert
    the user's own edits -- so the CLI passes them here, attributed through
    the same base inference the two-way guard uses."""
    if repo is not None and repo.has_conflicts():
        raise ApplyConflictError(
            "checkout has unresolved merge conflicts; resolve them "
            "(normal git tools) before apply")

    result = ApplyResult()
    session = BackupSession(backup_root)

    for d in diff_all(manifest, checkout, roots, box_tags):
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
            if skip and display in skip:
                result.skipped.append((display, skip[display]))
                continue
            # Deny-list guards BOTH directions: a HARD_DENY/manifest-denied
            # file committed in the payload must never reach the live tree
            # (tester run-02: apply had no deny awareness at all).
            pattern = is_denied(rel or src.name, manifest.deny)
            if pattern:
                repo_display = f"{d.entry.repo}/{rel}" if rel else d.entry.repo
                result.refused_denied.append((repo_display, pattern))
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
            # A file the checkout NEVER had is a local ADDITION, not something
            # the checkout deleted. Calling it a "removal pending" implied the
            # user had to run `collect` before `apply` was safe -- forcing an
            # order that is not actually required, over a file that was never
            # at risk. Git can tell the two apart, so ask it.
            repo_rel = f"{d.entry.repo}/{rel}" if rel else d.entry.repo
            if repo is not None and not repo.path_in_history(repo_rel):
                result.local_only.append(display)
                continue
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
        if not entry_applies(entry, box_tags):
            continue
        if only and not entry.repo.startswith(only):
            continue
        result.only_matched += 1
        live_base, repo_base = entry_bases(
            entry, checkout, roots, manifest.territories)
        pattern = is_denied(entry.repo, manifest.deny)
        if pattern:
            result.refused_denied.append((entry.repo, pattern))
            continue
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
