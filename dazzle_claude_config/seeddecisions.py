"""The per-box seed-decision record (issue #27).

A seeded file is the box's own after delivery, so when it differs from the
payload's CURRENT seed, that is a question, not drift: keep yours, or take
the new seed? This module records the user's answer so the question is
asked once and re-asked only when the upstream seed actually changes.

Deliberately thin, flat, hand-editable JSON -- the record is the user's own
sign-off and must stay openable, readable, and revocable in an editor (the
same storage idiom as ccs-box.json and ccs-config.json; the rationale and
the threshold where a database would become right are recorded on #27).

Schema (`~/claude/ccs-seed-decisions.json`):

    { "version": 1,
      "decisions": {
        "<entry target>": {
          "decision": "keep",
          "mode": "always" | "until-changed",
          "seed_blob": "<sha256 of the LF-normalized seed DECIDED AGAINST>",
          "date": "YYYY-MM-DD" } } }

`seed_blob` anchors the decision to the seed version the user saw:
"changed since you decided" is a hash comparison, never a date heuristic.
A malformed file is reported and treated as empty -- decisions can be
lost to corruption, never widened by it (the boxconfig rule).
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

VALID_MODES = ("always", "until-changed")


@dataclass
class Decisions:
    by_target: dict[str, dict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def decisions_path(user_claude: Path | None = None) -> Path:
    if user_claude is None:
        user_claude = Path(os.path.expanduser("~")) / "claude"
    return Path(user_claude) / "ccs-seed-decisions.json"


def load(user_claude: Path | None = None) -> Decisions:
    path = decisions_path(user_claude)
    if not path.is_file():
        return Decisions()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        return Decisions(errors=[f"{path.name}: not valid JSON ({e}); "
                                 "treating as no decisions"])
    if not isinstance(data, dict) or not isinstance(data.get("decisions"), dict):
        return Decisions(errors=[f"{path.name}: expected an object with a "
                                 "'decisions' object; treating as no decisions"])
    out = Decisions()
    for target, rec in data["decisions"].items():
        if not isinstance(rec, dict) or rec.get("decision") != "keep" \
                or rec.get("mode") not in VALID_MODES \
                or not isinstance(rec.get("seed_blob"), str):
            out.errors.append(f"{path.name}: entry {target!r} malformed; ignored")
            continue
        out.by_target[target] = rec
    return out


def _write(path: Path, decisions: dict[str, dict]) -> None:
    body = {"version": 1, "decisions": decisions}
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(body, f, indent=1)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def keep(target: str, mode: str, seed_blob: str,
         user_claude: Path | None = None) -> Path:
    """Record `keep` for one seed target against the seed it was decided on."""
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES}")
    path = decisions_path(user_claude)
    current = load(user_claude).by_target
    current[target] = {"decision": "keep", "mode": mode,
                       "seed_blob": seed_blob, "date": date.today().isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, current)
    return path


def reset(target: str, user_claude: Path | None = None) -> bool:
    """Forget the decision for one target. True if one existed."""
    path = decisions_path(user_claude)
    current = load(user_claude).by_target
    if target not in current:
        return False
    del current[target]
    _write(path, current)
    return True


# --------------------------------------------------------------- the states
# Moved here from cli._seed_findings on 2026-09-02 as a PURE MOVE (no
# semantic change) so that `merge` can read the same seven states `status`
# reports. Until then only the status verb consulted this record, and a bare
# `ccs merge` planned -- and opened a diff tool on -- seeded files the person
# had already decided to keep (the modularity design's G21, third instance).
# The state machine is the classifier the modularity design's unit 2.2 will
# fold into `ancestry.classify()`; it lives here until then.

#: States in which the seeded file is settled -- the person's own, and not a
#: pending question. `open` (customised, no decision) and `reopened` (the
#: upstream seed moved since the decision) are the two that still ask.
SETTLED = frozenset({"matches", "untouched-old", "kept-always", "kept-current"})


def norm_sha(data: bytes) -> str:
    """SHA-256 of the LF-normalised bytes: history stores LF, live Windows
    files are CRLF, and a raw comparison never matches anything."""
    import hashlib
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def findings(manifest, checkout, roots, repo, box_tags=frozenset(),
             user_claude: Path | None = None):
    """Per FILE seed entry, its state (issue #27): a list of
    (target, state, live_path, repo_path), plus the decision file's errors.

    States: absent (will seed) | matches | untouched-old (the payload
    replaced a seed this box never edited -- auto-offer --reseed, no
    question) | open (customized, no decision recorded) | kept-always |
    kept-current (until-changed, seed unchanged) | reopened (until-changed,
    the seed moved since the decision).

    `repo` may be None: then `untouched-old` cannot be told from `open`
    (it needs the seed's history), and such a file reads as `open`.

    EOL-insensitive throughout (measured -- see
    tests/one-offs/poc_seed_ancestry_probe.py).
    """
    from .syncmap import entry_applies, entry_bases
    dec = load(user_claude)
    out: list[tuple] = []
    # A seed entry can be the FALLBACK for a target a tag-gated copy entry
    # also delivers (machine.template.md seeds boxes that machines/<name>/
    # does not cover). Where the copy entry applies, the copy governs --
    # asking "yours or the payload's?" about that file here would be wrong.
    covered = {(e.territory, e.target) for e in manifest.entries
               if e.strategy == "copy" and entry_applies(e, box_tags)}
    for entry in manifest.seed_entries():
        if not entry_applies(entry, box_tags):
            continue
        if (entry.territory, entry.target) in covered:
            continue
        live_base, repo_base = entry_bases(
            entry, checkout, roots, manifest.territories)
        if not repo_base.is_file():
            continue    # directory seeds: per-file reporting is #28 follow-up
        if not live_base.is_file():
            out.append((entry.target, "absent", live_base, repo_base))
            continue
        try:
            live, seed = live_base.read_bytes(), repo_base.read_bytes()
        except OSError:
            continue
        current_sha = norm_sha(seed)
        live_sha = norm_sha(live)
        if live_sha == current_sha:
            out.append((entry.target, "matches", live_base, repo_base))
            continue
        hist = repo.seed_history(entry.repo) if repo is not None else []
        if live_sha in {sha for _c, sha in hist} - {current_sha}:
            out.append((entry.target, "untouched-old", live_base, repo_base))
            continue
        rec = dec.by_target.get(entry.target)
        state = ("open" if rec is None else
                 "kept-always" if rec.get("mode") == "always" else
                 "kept-current" if rec.get("seed_blob") == current_sha else
                 "reopened")
        out.append((entry.target, state, live_base, repo_base))
    return out, dec.errors
