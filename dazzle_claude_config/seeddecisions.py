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
