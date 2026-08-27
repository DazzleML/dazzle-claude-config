"""A hand-edited config must not fail silently.

Two defects, found by a duplication audit across the four user-territory
record modules rather than by a user report:

1. `userconfig.load()` read `ccs-config.json` as plain utf-8 while the other
   three records tolerate a BOM. The file is explicitly meant to be
   hand-edited, and the Windows editors people reach for write one -- so
   `{"auto_pull": true}` saved in Notepad read back as False, and every other
   preference reverted to its default too.

2. The decode error WAS recorded, in `_errors`, and nothing ever printed it.
   The box config warned about its own breakage on the line above; this one
   did not. So a typo'd config looked exactly like an absent config, which is
   the failure mode the whole "never silently widen or narrow" stance exists
   to prevent.
"""
from __future__ import annotations

import json
from pathlib import Path

from dazzle_claude_config import userconfig


def _write_config(user: Path, body: dict, bom: bool = False) -> None:
    user.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(body, indent=2).encode("utf-8")
    (user / "ccs-config.json").write_bytes((b"\xef\xbb\xbf" if bom else b"") + raw)


def test_a_bom_prefixed_config_is_read_not_discarded(tmp_path):
    """What Notepad writes must still configure the tool."""
    user = tmp_path / "user"
    _write_config(user, {"auto_pull": True}, bom=True)
    cfg = userconfig.load(user)
    assert cfg["auto_pull"] is True
    assert not cfg.get("_errors")


def test_the_same_config_without_a_bom_still_works(tmp_path):
    user = tmp_path / "user"
    _write_config(user, {"auto_pull": True}, bom=False)
    assert userconfig.load(user)["auto_pull"] is True


def test_a_bom_does_not_mask_a_genuine_syntax_error(tmp_path):
    """Tolerating the BOM must not tolerate broken JSON."""
    user = tmp_path / "user"
    user.mkdir(parents=True)
    (user / "ccs-config.json").write_bytes(b"\xef\xbb\xbf{ not json")
    cfg = userconfig.load(user)
    assert cfg["auto_pull"] is False          # falls back to the default...
    assert cfg["_errors"], "...but says so"


def test_every_setting_survives_a_bom_not_just_the_first(tmp_path):
    user = tmp_path / "user"
    _write_config(user, {"auto_pull": True, "status_max_lines": 99,
                         "require_current": True}, bom=True)
    cfg = userconfig.load(user)
    assert cfg["auto_pull"] is True
    assert cfg["status_max_lines"] == 99
    assert cfg["require_current"] is True
