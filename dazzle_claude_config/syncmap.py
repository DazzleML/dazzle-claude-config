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


def line_stats(live: Path, repo: Path) -> tuple[int, int, int, int]:
    """(only_live, changed_both, only_repo, regions) between two files.

    "N files differ on both sides" answers *how many files*, which reads as
    *how many differences* and is the first question anyone asks. This gives
    the line-level answer so the report says what actually diverged.

    EOL-normalised: on Windows the live tree is CRLF while git hands back LF,
    and without normalising every line looks changed -- the same defect that
    produced phantom drift, a bogus repo comparison, and an unusable merge.
    """
    import difflib
    try:
        a = _normalize_eol(live.read_bytes()).decode("utf-8", "replace").splitlines()
        b = _normalize_eol(repo.read_bytes()).decode("utf-8", "replace").splitlines()
    except OSError:
        return (0, 0, 0, 0)
    ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
           if o[0] != "equal"]
    only_live = sum(i2 - i1 for t, i1, i2, j1, j2 in ops if t == "delete")
    only_repo = sum(j2 - j1 for t, i1, i2, j1, j2 in ops if t == "insert")
    changed = sum(max(i2 - i1, j2 - j1) for t, i1, i2, j1, j2 in ops if t == "replace")
    return (only_live, changed, only_repo, len(ops))


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
    # Files the CHECKOUT already holds for this entry. Zero means the checkout
    # has never carried this entry, which is adoption rather than drift -- the
    # distinction additive gating needs (DWP-7), and NOT derivable from the
    # lists above, since files identical on both sides appear in none of them.
    repo_tracked: int = 0

    @property
    def clean(self) -> bool:
        # denied_live is intentionally NOT drift: deny-matched files can
        # never sync, so their presence is the intended state.
        return not (self.live_only or self.repo_only or self.modified
                    or self.mismatch)


def only_scope(only: str | None, repo: str) -> tuple[bool, str | None]:
    """Does `--only` reach this entry, and how much of it?

    Component-wise, never a string prefix: `dotclaude/skills` reaches the
    entry `dotclaude/skills` and everything under `dotclaude/skills/...`, and
    does NOT reach `dotclaude/skills-extra`. Returns (reached, sub_prefix):

      (True, None)    the whole entry (`--only` names it or a parent of it)
      (True, "a/b")   only files under a/b inside the entry -- `--only
                      dotclaude/skills/test-mutation` on the entry
                      `dotclaude/skills`. Until 0.4.3 this matched nothing,
                      silently: the filter was `entry.repo.startswith(only)`.
      (False, None)   not reached
    """
    if not only:
        return True, None
    o = only.replace(chr(92), "/").strip("/")
    r = repo.strip("/")
    if o == r or r.startswith(o + "/"):
        return True, None
    if o.startswith(r + "/"):
        return True, o[len(r) + 1:]
    return False, None


def rel_in_scope(rel: str, sub_prefix: str | None) -> bool:
    """Is a file (entry-relative path) inside the --only sub-prefix?"""
    if sub_prefix is None:
        return True
    r = rel.replace(chr(92), "/")
    return r == sub_prefix or r.startswith(sub_prefix + "/")


def scope_diff(d: "EntryDiff", sub_prefix: str | None) -> "EntryDiff":
    """A copy of an EntryDiff with every per-file list cut to the sub-prefix
    -- so the guard, the direction skips, --force, and the removal
    candidates all see the same, scoped, set of files."""
    if sub_prefix is None:
        return d
    import dataclasses
    return dataclasses.replace(
        d,
        live_only=[r for r in d.live_only if rel_in_scope(r, sub_prefix)],
        repo_only=[r for r in d.repo_only if rel_in_scope(r, sub_prefix)],
        modified=[r for r in d.modified if rel_in_scope(r, sub_prefix)],
        excluded=[r for r in d.excluded if rel_in_scope(r, sub_prefix)],
        denied_live=[r for r in d.denied_live if rel_in_scope(r, sub_prefix)],
    )


def entry_applies(entry: Entry, box_tags=frozenset()) -> bool:
    """Is this entry for this box? Its `os` must match and every tag it
    requires must be declared in the box's tags (boxconfig). The one
    predicate gates BOTH directions -- status, apply and collect all go
    through it -- so a tag-gated file is never spread by a box that lacks
    the tag. No tags given = no tag-gated entry applies: the safe default."""
    if entry.os is not None and entry.os != os_key():
        return False
    return set(entry.tags) <= set(box_tags)


def entry_gate_reason(entry: Entry, box_tags=frozenset()) -> str | None:
    """Why `entry_applies` said no, for status output; None when it applies."""
    if entry.os is not None and entry.os != os_key():
        return f"os: {entry.os}"
    missing = sorted(set(entry.tags) - set(box_tags))
    if missing:
        return "tags: " + ", ".join(missing)
    return None


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
        d.repo_tracked = 1 if repo_exists else 0
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
    d.repo_tracked = len(repo_files)

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
             roots: dict[str, Path], box_tags=frozenset()) -> list[EntryDiff]:
    return [
        diff_entry(e, checkout, roots, manifest)
        for e in manifest.copy_entries() if entry_applies(e, box_tags)
    ]
