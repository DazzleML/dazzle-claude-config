"""ccs-manifest.json loading and validation (manifest_version 1).

The manifest is a default-closed allowlist: only listed entries ever move.
Unknown top-level or entry keys are errors -- a typo must never silently
widen or narrow the sync surface.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_NAME = "ccs-manifest.json"
SUPPORTED_VERSION = 1

VALID_STRATEGIES = {"copy", "render", "seed-if-absent", "plugins"}
# Strategies the Phase 1 MVP executes; others are reported and skipped.
MVP_STRATEGIES = {"copy", "seed-if-absent"}

_TOP_KEYS = {"manifest_version", "description", "territories", "entries",
             "collect_exclude", "deny"}
_ENTRY_KEYS = {"repo", "territory", "target", "strategy", "overlays", "vars", "os"}
_TERRITORY_KEYS = {"root_var", "repo_dir"}


class ManifestError(ValueError):
    pass


@dataclass
class Entry:
    repo: str
    strategy: str
    territory: str | None = None
    target: str | None = None
    overlays: list[str] = field(default_factory=list)
    vars: list[str] = field(default_factory=list)
    os: str | None = None


@dataclass
class Manifest:
    version: int
    territories: dict[str, dict[str, str]]
    entries: list[Entry]
    collect_exclude: list[str]
    deny: list[str]
    path: Path

    @classmethod
    def load(cls, checkout: Path) -> "Manifest":
        mpath = checkout / MANIFEST_NAME
        if not mpath.is_file():
            raise ManifestError(f"manifest not found: {mpath}")
        try:
            data = json.loads(mpath.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as e:
            raise ManifestError(f"manifest is not valid JSON: {mpath}: {e}") from e

        unknown = set(data) - _TOP_KEYS
        if unknown:
            raise ManifestError(f"unknown manifest keys: {sorted(unknown)}")
        version = data.get("manifest_version")
        if version != SUPPORTED_VERSION:
            raise ManifestError(
                f"unsupported manifest_version {version!r} (supported: {SUPPORTED_VERSION})")

        territories = data.get("territories") or {}
        for name, t in territories.items():
            unknown = set(t) - _TERRITORY_KEYS
            if unknown:
                raise ManifestError(f"territory {name!r}: unknown keys {sorted(unknown)}")
            for req in _TERRITORY_KEYS:
                if req not in t:
                    raise ManifestError(f"territory {name!r}: missing {req!r}")

        entries: list[Entry] = []
        for i, e in enumerate(data.get("entries") or []):
            unknown = set(e) - _ENTRY_KEYS
            if unknown:
                raise ManifestError(f"entry {i}: unknown keys {sorted(unknown)}")
            strategy = e.get("strategy")
            if strategy not in VALID_STRATEGIES:
                raise ManifestError(f"entry {i}: invalid strategy {strategy!r}")
            if "repo" not in e:
                raise ManifestError(f"entry {i}: missing 'repo'")
            if strategy != "plugins":
                for req in ("territory", "target"):
                    if req not in e:
                        raise ManifestError(f"entry {i}: missing {req!r}")
                if e["territory"] not in territories:
                    raise ManifestError(
                        f"entry {i}: unknown territory {e['territory']!r}")
            entries.append(Entry(
                repo=e["repo"], strategy=strategy, territory=e.get("territory"),
                target=e.get("target"), overlays=list(e.get("overlays") or []),
                vars=list(e.get("vars") or []), os=e.get("os")))

        return cls(
            version=version,
            territories=territories,
            entries=entries,
            collect_exclude=list(data.get("collect_exclude") or []),
            deny=list(data.get("deny") or []),
            path=mpath,
        )

    def copy_entries(self) -> list[Entry]:
        return [e for e in self.entries if e.strategy == "copy"]

    def seed_entries(self) -> list[Entry]:
        return [e for e in self.entries if e.strategy == "seed-if-absent"]

    def deferred_entries(self) -> list[Entry]:
        """Entries whose strategies the MVP does not execute (render, plugins)."""
        return [e for e in self.entries if e.strategy not in MVP_STRATEGIES]
