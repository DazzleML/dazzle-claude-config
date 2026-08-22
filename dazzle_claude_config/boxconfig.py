"""What this box IS -- its declared name and tags -- distinct from how ccs
should behave (userconfig) and from what syncs where (the manifest).

A manifest entry may carry ``"tags": [...]``; it applies on a box only when
every one of those tags is declared here. That single predicate delivers
platform files, per-box files (``machines/<name>/``) and opt-in addenda, in
BOTH directions: a tag-gated entry is never applied on a box that lacks the
tag, and never collected from it either, so a box-only file cannot spread.

Tags are declared names, never hostnames: ``zeromeld-org`` is a choice the
operator made, not something derived from DNS that a rename would break.

Lives in user territory (``~/claude/ccs-box.json``), beside ccs-config.json.
A missing file means "no tags" -- every tag-gated entry stays off, which is
the safe direction. A malformed file is reported, never raised, and also
means "no tags": a broken declaration must narrow the sync surface, never
widen it.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

BOX_FILE = "ccs-box.json"
_KEYS = {"name", "tags"}
#: A tag is a short lowercase token: letters, digits, ``-``, ``_``, ``.``.
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
ENV_TAGS = "CCS_BOX_TAGS"


@dataclass
class Box:
    name: str | None = None
    tags: frozenset[str] = frozenset()
    path: str = ""
    errors: list[str] = field(default_factory=list)

    def has_all(self, wanted) -> bool:
        return set(wanted) <= self.tags


def box_path(user_claude: Path | None = None) -> Path:
    if user_claude is None:
        user_claude = Path(os.path.expanduser("~")) / "claude"
    return Path(user_claude) / BOX_FILE


def validate_tags(raw, where: str) -> tuple[list[str], list[str]]:
    """(tags, errors). Rejects non-lists, non-strings and malformed tokens."""
    errors: list[str] = []
    if not isinstance(raw, list):
        return [], [f"{where}: 'tags' must be a list of strings"]
    tags: list[str] = []
    for t in raw:
        if not isinstance(t, str) or not TAG_RE.match(t):
            errors.append(
                f"{where}: bad tag {t!r} (lowercase letters, digits, '-', '_', '.')")
            continue
        tags.append(t)
    return tags, errors


def load(user_claude: Path | None = None) -> Box:
    """Read ``~/claude/ccs-box.json``; ``CCS_BOX_TAGS`` (comma-separated)
    replaces the file's tags when set, for tests and one-off runs."""
    path = box_path(user_claude)
    box = Box(path=str(path))
    tags: list[str] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as e:
            box.errors.append(f"{path}: {e.__class__.__name__}: {e}")
            data = None
        if data is not None:
            if not isinstance(data, dict):
                box.errors.append(f"{path}: expected a JSON object")
            else:
                unknown = set(data) - _KEYS
                if unknown:
                    box.errors.append(
                        f"{path}: unknown keys {sorted(unknown)} (ignored)")
                name = data.get("name")
                if name is not None:
                    if isinstance(name, str) and TAG_RE.match(name):
                        box.name = name
                    else:
                        box.errors.append(f"{path}: bad name {name!r}")
                if "tags" in data:
                    tags, errs = validate_tags(data["tags"], str(path))
                    box.errors.extend(errs)

    env = os.environ.get(ENV_TAGS)
    if env is not None:
        tags, errs = validate_tags(
            [t.strip() for t in env.split(",") if t.strip()], ENV_TAGS)
        box.errors.extend(errs)

    box.tags = frozenset(tags)
    return box


def write_template(user_claude: Path | None = None, name: str | None = None) -> Path:
    """Write a starter declaration. Never overwrites an existing file."""
    path = box_path(user_claude)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"name": name, "tags": [name] if name else []}
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return path
