"""Secrets defense: denied filenames and credential-shaped content.

Layers 2-3 of the guard stack (manifest allowlist is layer 1; the payload
repo's pre-push hook and repo visibility are layers 4-5). Deny rules are
hard-coded here IN ADDITION to the manifest's deny list: even a manifest
that lists a credential file gets refused.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

# Never-sync filenames/patterns, enforced regardless of manifest contents.
HARD_DENY = [
    ".credentials.json",
    ".claude.json",
    "settings.local.json",
    "installed_plugins.json",
    "known_marketplaces.json",
    "*.db",
    "*.db-*",
    "history.jsonl",
]

SECRET_RE = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{10,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[bpoas]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)

# Files larger than this are not content-scanned (payload files are small;
# anything bigger is almost certainly not hand-edited config).
_SCAN_SIZE_CAP = 10 * 1024 * 1024


@dataclass
class SecretHit:
    rel_path: str
    line_no: int
    excerpt: str


def _match_one(rel: str, pattern: str) -> bool:
    """Match pattern against the posix-style relative path or any segment/basename."""
    p = PurePosixPath(rel)
    if fnmatch.fnmatch(rel, pattern):
        return True
    if fnmatch.fnmatch(p.name, pattern):
        return True
    # Directory patterns like '**/cache/**' or 'commands/deprecated/**'
    stripped = pattern.strip("*/")
    if stripped and stripped in p.parts[:-1]:
        return True
    if pattern.endswith("/**") and rel.startswith(pattern[:-3].lstrip("*/") ):
        return True
    return False


def is_denied(rel: str, extra_deny: list[str] | None = None) -> str | None:
    """Return the matching deny pattern if rel is denied, else None."""
    for pattern in list(extra_deny or []) + HARD_DENY:
        if _match_one(rel, pattern):
            return pattern
    return None


def is_excluded(rel: str, exclude: list[str]) -> str | None:
    for pattern in exclude:
        if _match_one(rel, pattern):
            return pattern
    return None


def scan_file(path, rel: str) -> list[SecretHit]:
    """Scan a file for credential-shaped content.

    ALL suffixes are scanned (a secret smuggled into screenshot.png must
    not sail through); binary bytes decode via errors="replace" and the
    token regexes simply don't match true binary noise. Files over the
    size cap are skipped.

    Note: deny/exclude PATTERN matching (fnmatch) is case-insensitive on
    Windows and case-sensitive on POSIX -- name patterns should assume
    case-sensitive matching to be safe on all platforms.
    """
    try:
        if path.stat().st_size > _SCAN_SIZE_CAP:
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits = []
    for no, line in enumerate(text.splitlines(), 1):
        m = SECRET_RE.search(line)
        if m:
            excerpt = m.group(0)[:12] + "..."
            hits.append(SecretHit(rel_path=rel, line_no=no, excerpt=excerpt))
    return hits
