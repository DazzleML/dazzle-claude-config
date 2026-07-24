"""Shared comparison core: what differs between the live tree and the checkout.

Used by collect (live -> checkout), apply (checkout -> live), status, and diff,
so all four verbs agree about what "drift" means.
"""
from __future__ import annotations

import filecmp
from dataclasses import dataclass, field
from pathlib import Path

from .manifest import Entry, Manifest
from .platform_info import os_key
from .secrets import is_excluded


def iter_files(base: Path) -> list[str]:
    """All files under base as sorted posix-style relative paths."""
    if base.is_file():
        return [""]  # single-file entry; rel handled by caller
    if not base.exists():
        return []
    return sorted(
        p.relative_to(base).as_posix()
        for p in base.rglob("*") if p.is_file())


def files_differ(a: Path, b: Path) -> bool:
    return not filecmp.cmp(str(a), str(b), shallow=False)


@dataclass
class EntryDiff:
    entry: Entry
    live_base: Path
    repo_base: Path
    live_only: list[str] = field(default_factory=list)   # collect-pending adds
    repo_only: list[str] = field(default_factory=list)   # apply-pending adds / removals
    modified: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.live_only or self.repo_only or self.modified)


def entry_applies(entry: Entry) -> bool:
    return entry.os is None or entry.os == os_key()


def entry_bases(entry: Entry, checkout: Path, roots: dict[str, Path],
                territories: dict[str, dict[str, str]]) -> tuple[Path, Path]:
    """(live_base, repo_base) for an entry. Single files map file-to-file."""
    root_var = territories[entry.territory]["root_var"]
    live_base = roots[root_var] / entry.target
    repo_base = checkout / entry.repo
    return live_base, repo_base


def diff_entry(entry: Entry, checkout: Path, roots: dict[str, Path],
               manifest: Manifest) -> EntryDiff:
    live_base, repo_base = entry_bases(entry, checkout, roots, manifest.territories)
    d = EntryDiff(entry=entry, live_base=live_base, repo_base=repo_base)

    # Single-file entry
    if repo_base.is_file() or (not repo_base.exists() and live_base.is_file()):
        live_exists, repo_exists = live_base.is_file(), repo_base.is_file()
        if live_exists and not repo_exists:
            d.live_only.append("")
        elif repo_exists and not live_exists:
            d.repo_only.append("")
        elif live_exists and repo_exists and files_differ(live_base, repo_base):
            d.modified.append("")
        return d

    live_files = set(iter_files(live_base))
    repo_files = set(iter_files(repo_base))

    for rel in sorted(live_files):
        full_rel = f"{entry.target}/{rel}" if rel else entry.target
        pattern = is_excluded(full_rel, manifest.collect_exclude) or \
            is_excluded(rel, manifest.collect_exclude)
        if pattern:
            d.excluded.append(rel)
            continue
        if rel not in repo_files:
            d.live_only.append(rel)
        elif files_differ(live_base / rel, repo_base / rel):
            d.modified.append(rel)
    for rel in sorted(repo_files - live_files):
        d.repo_only.append(rel)
    return d


def diff_all(manifest: Manifest, checkout: Path,
             roots: dict[str, Path]) -> list[EntryDiff]:
    return [
        diff_entry(e, checkout, roots, manifest)
        for e in manifest.copy_entries() if entry_applies(e)
    ]
