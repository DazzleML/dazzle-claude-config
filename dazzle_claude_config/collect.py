"""collect: live tree -> payload checkout, through the guard stack.

Pipeline per file: allowlist (manifest entry) -> collect_exclude ->
deny-list (manifest deny + HARD_DENY, refusals REPORTED) -> secret scan
(refusals reported) -> copy. Afterwards, A8: verify the copied paths are
not ignored/excluded by git (machine-level info/exclude injections).
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .gitops import CheckoutRepo
from .manifest import Manifest
from .secrets import SecretHit, is_denied, scan_file
from .syncmap import diff_all


@dataclass
class CollectResult:
    copied: list[str] = field(default_factory=list)
    refused_denied: list[tuple[str, str]] = field(default_factory=list)   # (rel, pattern)
    refused_secrets: list[SecretHit] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    missing_live: list[str] = field(default_factory=list)  # in repo, gone locally (report only)
    git_ignored: list[str] = field(default_factory=list)   # A8 violations

    @property
    def refusals(self) -> int:
        return len(self.refused_denied) + len(self.refused_secrets)


def collect(manifest: Manifest, checkout: Path, roots: dict[str, Path],
            repo: CheckoutRepo | None = None, dry_run: bool = False) -> CollectResult:
    result = CollectResult()
    copied_repo_rels: list[str] = []

    for d in diff_all(manifest, checkout, roots):
        result.excluded.extend(f"{d.entry.repo}/{r}" for r in d.excluded)
        result.missing_live.extend(f"{d.entry.repo}/{r}" if r else d.entry.repo
                                   for r in d.repo_only)
        for rel in d.live_only + d.modified:
            src = d.live_base / rel if rel else d.live_base
            display = f"{d.entry.repo}/{rel}" if rel else d.entry.repo

            pattern = is_denied(rel or src.name, manifest.deny)
            if pattern:
                result.refused_denied.append((display, pattern))
                continue
            hits = scan_file(src, display)
            if hits:
                result.refused_secrets.extend(hits)
                continue

            if not dry_run:
                dest = d.repo_base / rel if rel else d.repo_base
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            result.copied.append(display)
            repo_rel = f"{d.entry.repo}/{rel}" if rel else d.entry.repo
            copied_repo_rels.append(repo_rel)

    # A8: copied files must be visible to git, or they will silently drop
    # out of the payload (Phase 0 incident: unanchored CLAUDE.md in a
    # machine-level info/exclude).
    if repo is not None and copied_repo_rels and not dry_run:
        result.git_ignored = repo.check_ignored(copied_repo_rels)

    return result
