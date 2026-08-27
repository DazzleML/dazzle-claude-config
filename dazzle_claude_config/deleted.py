"""Files the user deleted from live ON PURPOSE, so `apply` stops restoring them (#12).

Why this is a RECORD and not an inference:

A path that exists in the checkout and is absent from live has two readings --
"you deleted it" and "it never reached this box" -- and they call for opposite
actions. Issue #12 proposed telling them apart with `git log -- <path>`, and
that does not work: the checkout's history contains every checkout file, so it
answers "did the CHECKOUT ever have this", never "did LIVE ever have this".
Nothing inside the checkout can answer the second question. Only a record of
what this box applied could, which is #14's sync point -- days of work, and
still an inference of intent rather than a statement of it.

So ccs asks instead. `apply` reports what it is about to install into a live
tree that lacks it, and names the command that records "I removed that on
purpose". The record is the user's word, not the tool's guess, which is the
one form of this answer that cannot be wrong.

Storage mirrors ccs-seed-decisions.json exactly: hand-editable JSON in user
territory, validated before trust, malformed entries warned about and skipped
rather than crashing a sync.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Deletions:
    targets: dict[str, dict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def holds(self, target: str) -> bool:
        return target in self.targets


def deletions_path(user_claude: Path | None = None) -> Path:
    if user_claude is None:
        user_claude = Path(os.path.expanduser("~")) / "claude"
    return Path(user_claude) / "ccs-deleted.json"


def load(user_claude: Path | None = None) -> Deletions:
    path = deletions_path(user_claude)
    if not path.is_file():
        return Deletions()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        return Deletions(errors=[f"{path.name}: not valid JSON ({e}); "
                                 "treating as no recorded deletions"])
    if not isinstance(data, dict) or not isinstance(data.get("deleted"), dict):
        return Deletions(errors=[f"{path.name}: expected an object with a "
                                 "'deleted' object; treating as none"])
    out = Deletions()
    for target, rec in data["deleted"].items():
        if not isinstance(rec, dict) or rec.get("decision") != "keep-deleted":
            out.errors.append(f"{path.name}: entry {target!r} malformed; ignored")
            continue
        out.targets[target] = rec
    return out


def _write(path: Path, deleted: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "_comment": ("Files you removed from your live config on purpose. "
                     "ccs apply will not put them back. Delete an entry here "
                     "to let it be installed again."),
        "deleted": deleted,
    }
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(body, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def keep_deleted(target: str, user_claude: Path | None = None) -> Path:
    """Record that `target` was removed on purpose. Returns the record path."""
    path = deletions_path(user_claude)
    current = load(user_claude)
    current.targets[target] = {"decision": "keep-deleted"}
    _write(path, current.targets)
    return path


def restore(target: str, user_claude: Path | None = None) -> bool:
    """Forget the record for `target`, so apply installs it again."""
    current = load(user_claude)
    if target not in current.targets:
        return False
    del current.targets[target]
    _write(deletions_path(user_claude), current.targets)
    return True
