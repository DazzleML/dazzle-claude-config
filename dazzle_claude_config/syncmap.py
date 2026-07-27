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
from .secrets import is_denied, is_excluded


def iter_files(base: Path) -> list[str]:
    """All files under base as sorted posix-style relative paths."""
    if base.is_file():
        return [""]  # single-file entry; rel handled by caller
    if not base.exists():
        return []
    return sorted(
        p.relative_to(base).as_posix()
        for p in base.rglob("*") if p.is_file())


#: Read size for the text/binary sniff. A NUL byte in the first chunk is the
#: same heuristic git uses to decide a file is binary.
_SNIFF = 8000


def _looks_binary(blob: bytes) -> bool:
    return b"\x00" in blob[:_SNIFF]


def _normalize_eol(blob: bytes) -> bytes:
    return blob.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def files_differ(a: Path, b: Path) -> bool:
    """True when two files differ in content, ignoring line-ending style.

    Byte comparison alone reports drift that does not exist. A repo with
    `* text=auto` stores LF and checks out CRLF on Windows, while the live tree
    holds whatever wrote it -- usually LF. Every text file then reads as
    modified on every Windows machine, permanently. Measured on a real payload:
    67 files flagged, only 20 of which had any content change.

    That noise is not merely cosmetic. It buries real edits, makes `collect`
    rewrite dozens of untouched files, and trains the operator to skim a report
    that is supposed to be the safety check before files move.

    So compare the way git does: normalise line endings for text, compare bytes
    for binary. Comparison only -- nothing here rewrites a file. Making `apply`
    canonicalise line endings would mean editing live config to fix a reporting
    problem, which is a worse trade.
    """
    try:
        # Cheap accept: identical bytes are identical content, whatever the type.
        if filecmp.cmp(str(a), str(b), shallow=False):
            return False
        ba, bb = a.read_bytes(), b.read_bytes()
    except OSError:
        # Missing or unreadable is not "the same" -- report drift so the caller
        # surfaces it, rather than raising here or silently claiming a match.
        return True

    # Binary files must not be normalised; a NUL-safe byte could be content.
    if _looks_binary(ba) or _looks_binary(bb):
        return True

    return _normalize_eol(ba) != _normalize_eol(bb)


@dataclass
class EntryDiff:
    entry: Entry
    live_base: Path
    repo_base: Path
    live_only: list[str] = field(default_factory=list)   # collect-pending adds
    repo_only: list[str] = field(default_factory=list)   # apply-pending adds / removals
    modified: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    denied_live: list[str] = field(default_factory=list)  # deny-matched, never sync
    mismatch: str | None = None  # file-vs-directory type conflict; entry skipped
    total: int = 0  # files examined on either side (for "N files compared")

    @property
    def clean(self) -> bool:
        # denied_live is intentionally NOT drift: deny-matched files can
        # never sync, so their presence is the intended state.
        return not (self.live_only or self.repo_only or self.modified
                    or self.mismatch)


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

    # A file where a directory should be (or vice versa) is an environment
    # anomaly -- processing it would nest a file inside the same-named
    # checkout dir. Flag and skip the entry instead.
    if live_base.exists() and repo_base.exists() and \
            live_base.is_dir() != repo_base.is_dir():
        live_kind = "dir" if live_base.is_dir() else "file"
        repo_kind = "dir" if repo_base.is_dir() else "file"
        d.mismatch = f"type mismatch: live is a {live_kind}, repo is a {repo_kind}"
        return d

    # Single-file entry
    if repo_base.is_file() or (not repo_base.exists() and live_base.is_file()):
        live_exists, repo_exists = live_base.is_file(), repo_base.is_file()
        d.total = 1 if (live_exists or repo_exists) else 0
        if live_exists and not repo_exists:
            d.live_only.append("")
        elif repo_exists and not live_exists:
            d.repo_only.append("")
        elif live_exists and repo_exists and files_differ(live_base, repo_base):
            d.modified.append("")
        return d

    live_files = set(iter_files(live_base))
    repo_files = set(iter_files(repo_base))
    d.total = len(live_files | repo_files)

    for rel in sorted(live_files):
        full_rel = f"{entry.target}/{rel}" if rel else entry.target
        pattern = is_excluded(full_rel, manifest.collect_exclude) or \
            is_excluded(rel, manifest.collect_exclude)
        if pattern:
            d.excluded.append(rel)
            continue
        if rel not in repo_files:
            if is_denied(rel, manifest.deny):
                d.denied_live.append(rel)
            else:
                d.live_only.append(rel)
        elif files_differ(live_base / rel, repo_base / rel):
            d.modified.append(rel)
    for rel in sorted(repo_files - live_files):
        # Exclusion is symmetric: an excluded path is invisible to sync on
        # BOTH sides (e.g. __pycache__ generated in the checkout by git
        # hooks must not read as apply-pending drift).
        full_rel = f"{entry.target}/{rel}" if rel else entry.target
        if is_excluded(full_rel, manifest.collect_exclude) or \
                is_excluded(rel, manifest.collect_exclude):
            d.excluded.append(rel)
            continue
        d.repo_only.append(rel)
    return d


def diff_all(manifest: Manifest, checkout: Path,
             roots: dict[str, Path]) -> list[EntryDiff]:
    return [
        diff_entry(e, checkout, roots, manifest)
        for e in manifest.copy_entries() if entry_applies(e)
    ]
