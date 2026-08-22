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

# Standard ~/.claude surfaces recognized when a repo has no manifest (a bare
# mirror of someone's config dir). settings.local.json and credentials remain
# hard-denied regardless.
IMPLICIT_DIRS = ["agents", "skills", "commands", "hooks", "scripts", "output-styles"]
IMPLICIT_FILES = ["CLAUDE.md", "settings.json", "keybindings.json"]
_IMPLICIT_MARKERS = {"CLAUDE.md", "skills", "commands", "agents", "settings.json"}

_TOP_KEYS = {"manifest_version", "description", "territories", "entries",
             "collect_exclude", "deny", "hold_additions"}
_ENTRY_KEYS = {"repo", "territory", "target", "strategy", "overlays", "vars", "os",
               "tags"}
#: The only values `os` may take. An unknown value used to parse fine and then
#: never apply anywhere -- a typo silently removed the entry from every box.
VALID_OS = {"windows", "posix"}
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
    # Declared box tags this entry requires, ALL of them (see boxconfig).
    # Empty means "every box", which is what every pre-tags entry meant.
    tags: list[str] = field(default_factory=list)


@dataclass
class Manifest:
    version: int
    territories: dict[str, dict[str, str]]
    entries: list[Entry]
    collect_exclude: list[str]
    deny: list[str]
    path: Path
    # When true, `collect` updates files the checkout already tracks but will
    # NOT create new ones without --add. Set it on a payload that gets pushed
    # somewhere public or shared, where an unintended new file is published
    # rather than merely copied. Defaults false: existing private checkouts
    # keep the additive behavior they were built around.
    hold_additions: bool = False

    @classmethod
    def implicit(cls, checkout: Path) -> "Manifest":
        """Synthesize a manifest for a manifest-less checkout that is a bare
        mirror of a ~/.claude directory (the layout-agnostic case: any repo
        someone made by pushing their config dir as-is)."""
        present = {p.name for p in checkout.iterdir()} if checkout.is_dir() else set()
        if not (present & _IMPLICIT_MARKERS):
            # An EMPTY checkout is the new-payload case, not a wrong-repo
            # case -- someone cloned the fresh repo they just created. Tell
            # them how to seed it rather than only what is missing.
            if not (present - {".git"}):
                raise ManifestError(
                    f"{checkout} is an empty repo. To turn it into a payload, "
                    "create the surfaces you want tracked, then collect:\n"
                    "    mkdir skills commands agents      (whichever you use)\n"
                    "    touch CLAUDE.md                   (if you want your memory synced)\n"
                    "    ccs collect                       (copies your live config in, guarded)\n"
                    "    git add -A && git commit -m 'my config' && git push")
            raise ManifestError(
                f"no {MANIFEST_NAME} and {checkout} does not look like a "
                "Claude config dir (none of: " + ", ".join(sorted(_IMPLICIT_MARKERS)) + ")")
        entries = []
        for d in IMPLICIT_DIRS:
            if (checkout / d).is_dir():
                entries.append(Entry(repo=d, strategy="copy",
                                     territory="dotclaude", target=d))
        for f in IMPLICIT_FILES:
            if (checkout / f).is_file():
                entries.append(Entry(repo=f, strategy="copy",
                                     territory="dotclaude", target=f))
        if not entries:
            # Marker NAMES matched but none is its expected type (e.g. a
            # directory literally named CLAUDE.md). Without this, every verb
            # would report a misleadingly healthy "clean"/"nothing to do"
            # while tracking nothing at all (tester run-02).
            found = sorted(present & _IMPLICIT_MARKERS)
            raise ManifestError(
                f"no {MANIFEST_NAME} and {checkout} has config-marker names "
                f"({', '.join(found)}) but none is usable as its expected type "
                "(file vs directory) -- refusing to treat it as a config mirror")
        return cls(
            version=SUPPORTED_VERSION,
            territories={"dotclaude": {"root_var": "CLAUDE_DIR", "repo_dir": "."}},
            entries=entries,
            # Nested git repos inside a mirror (a skill cloned straight into
            # skills/, etc.) are never config -- invisible to sync in both
            # directions, matching __pycache__ handling.
            collect_exclude=["**/__pycache__/**", "**/*.pyc", "**/.git/**",
                             "**/.vscode/**", "**/.idea/**", "**/.DS_Store"],
            deny=[],
            path=checkout / MANIFEST_NAME,
        )

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
            os_val = e.get("os")
            if os_val is not None and os_val not in VALID_OS:
                raise ManifestError(
                    f"entry {i}: invalid os {os_val!r} (valid: {sorted(VALID_OS)})")
            tags: list[str] = []
            if "tags" in e:
                from .boxconfig import validate_tags
                tags, errs = validate_tags(e["tags"], f"entry {i}")
                if errs:
                    raise ManifestError("; ".join(errs))
            entries.append(Entry(
                repo=e["repo"], strategy=strategy, territory=e.get("territory"),
                target=e.get("target"), overlays=list(e.get("overlays") or []),
                vars=list(e.get("vars") or []), os=os_val, tags=tags))

        return cls(
            version=version,
            territories=territories,
            entries=entries,
            collect_exclude=list(data.get("collect_exclude") or []),
            deny=list(data.get("deny") or []),
            path=mpath,
            hold_additions=bool(data.get("hold_additions") or False),
        )

    def copy_entries(self) -> list[Entry]:
        return [e for e in self.entries if e.strategy == "copy"]

    def seed_entries(self) -> list[Entry]:
        return [e for e in self.entries if e.strategy == "seed-if-absent"]

    def deferred_entries(self) -> list[Entry]:
        """Entries whose strategies the MVP does not execute (render, plugins)."""
        return [e for e in self.entries if e.strategy not in MVP_STRATEGIES]
