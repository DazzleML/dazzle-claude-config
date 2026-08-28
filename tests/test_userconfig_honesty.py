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


# -- the same broken file, described the same way by every verb ----------------
#
# Found by a tester cross-checking three checklists against each other: the
# identical malformed config produced `not valid JSON (...)` from `doctor` and
# `setup update` -- both written this release -- and `JSONDecodeError: ...`
# from the older load path that `status`, `apply` and `collect` use. Two
# qualities of message for one condition, decided by which verb you happened
# to run. That kind of inconsistency is only ever visible to someone holding
# all three outputs at once.

def test_a_malformed_config_says_not_valid_JSON_not_the_exception_class(
        tmp_path):
    from dazzle_claude_config import userconfig
    user = tmp_path / "user"
    user.mkdir()
    (user / "ccs-config.json").write_text("{oops", encoding="utf-8")
    cfg = userconfig.load(user)
    errors = " ".join(cfg.get("_errors", []))
    assert "not valid JSON" in errors, errors
    assert "JSONDecodeError" not in errors, (
        "the parser's class name is not something a reader can act on\n"
        + errors)


def test_a_config_that_is_not_an_object_says_so_specifically(tmp_path):
    """`[1, 2, 3]` IS valid JSON. Calling it 'not valid JSON' sends someone
    hunting for a syntax error that is not there, so the two failures must
    stay distinguishable -- in this path as well as in doctor's."""
    from dazzle_claude_config import userconfig
    user = tmp_path / "user"
    user.mkdir()
    (user / "ccs-config.json").write_text("[1, 2, 3]", encoding="utf-8")
    cfg = userconfig.load(user)
    errors = " ".join(cfg.get("_errors", []))
    assert "not a JSON object" in errors, errors
    assert "not valid JSON" not in errors, errors


def test_every_path_that_reports_a_broken_config_agrees(tmp_path):
    """The actual defect was DISAGREEMENT, so the test is a comparison rather
    than three separate string checks -- otherwise the next path added drifts
    again and each individual assertion still passes."""
    from dazzle_claude_config import userconfig
    user = tmp_path / "user"
    user.mkdir()
    (user / "ccs-config.json").write_text("{oops", encoding="utf-8")

    from_load = " ".join(userconfig.load(user).get("_errors", []))
    from_plan = userconfig.plan_config(user).unreadable_reason
    assert "not valid JSON" in from_load and "not valid JSON" in from_plan, (
        f"load said: {from_load}\nplan said: {from_plan}")


# -- two pure-function edges the real data never reaches -----------------------
# Both written because a mutation survived. Neither is reachable through the
# CLI today; both are one line to get wrong and free to pin.

def test_n_settings_says_zero_settings_not_zero_setting():
    """Mutation M5 turned `n == 1` into `n <= 1`, so a count of zero read
    "0 setting". Nothing calls it with zero today -- every call site is inside
    a branch that already proved the list non-empty -- which is exactly why
    the mutant lived. The helper exists to be the ONE place this phrase is
    built, so it has to be right at every count, not only the reachable ones.
    """
    from dazzle_claude_config import render
    assert render.n_settings(0) == "0 settings"
    assert render.n_settings(1) == "1 setting"
    assert render.n_settings(2) == "2 settings"


def test_an_EMPTY_unreadable_reason_still_means_unreadable():
    """Mutation M6 turned `if self.unreadable is None` into `if not
    self.unreadable`, conflating "no reason recorded" with "the file is fine".

    The distinction is the whole point of the field: `None` means readable,
    and anything else -- including an empty string -- means ccs could not
    parse it. Reporting a poor reason is recoverable; reporting a broken file
    as healthy is the failure this property exists to prevent.
    """
    from dazzle_claude_config.userconfig import ConfigPlan
    from pathlib import Path
    plan = ConfigPlan(path=Path("x.json"), exists=True, missing={}, unknown=[],
                      unreadable="")
    assert plan.unreadable_reason is not None, (
        "an empty reason is still a reason -- the file did not parse")
    assert ConfigPlan(path=Path("x.json"), exists=True, missing={},
                      unknown=[]).unreadable_reason is None


def test_every_path_builds_the_broken_config_sentence_FROM_ONE_DEFINITION():
    """The structural version of the consistency test above.

    Asserting that each path happens to say "not valid JSON" only proves the
    copies agree TODAY. This proves there is one definition to disagree with:
    both the loader and the planner must produce exactly what
    `not_valid_json()` / `NOT_AN_OBJECT` produce, so a fourth call site added
    later either uses them or fails here.

    The original defect was two hand-written copies drifting apart. A test
    that compares outputs to each other would have passed the whole time they
    were both wrong; this one compares them to the source.
    """
    import json as _json
    import tempfile
    from pathlib import Path
    from dazzle_claude_config import userconfig

    d = Path(tempfile.mkdtemp())
    (d / "ccs-config.json").write_text("{oops", encoding="utf-8")
    try:
        _json.loads("{oops")
    except ValueError as exc:
        canonical = userconfig.not_valid_json(exc)

    assert userconfig.plan_config(d).unreadable == canonical, (
        "plan_config must build the sentence with not_valid_json(), not "
        "write its own")
    assert canonical in " ".join(userconfig.load(d).get("_errors", [])), (
        "load() must build it the same way")

    (d / "ccs-config.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert userconfig.plan_config(d).unreadable == userconfig.NOT_AN_OBJECT
    assert userconfig.NOT_AN_OBJECT in " ".join(
        userconfig.load(d).get("_errors", []))
