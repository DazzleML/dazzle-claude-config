"""`ccs setup update` -- teach an existing config the keys this ccs knows (#32).

A config file freezes at the schema of whatever version wrote it, and nothing
ever updated it. That was a latent annoyance until v0.5.10 added
`sync_removals`, a default that MOVES FILES -- at which point a machine could
acquire a file-touching behaviour on upgrade with nothing in its own config
recording that the setting exists.

Shaped after `csb setup update`: it ACTS where the action has exactly one right
answer, and names the exact remedy where it does not. Adding a missing key at
its documented default is such an action, so there is no per-key prompt.

The load-bearing property is ADDITIVE-ONLY, and the subtle half of it is
`test_a_value_the_user_set_TO_THE_DEFAULT_is_not_rewritten`: a key whose value
happens to equal the default is indistinguishable from one ccs wrote, and must
be left alone anyway. The rule is about provenance, not equality.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dazzle_claude_config import userconfig
from dazzle_claude_config.cli import main


def _ccs(user: Path, *rest) -> list[str]:
    return ["--user-claude", str(user), "--no-color", "setup", "update", *rest]


def _write(user: Path, body: dict) -> Path:
    user.mkdir(parents=True, exist_ok=True)
    p = user / "ccs-config.json"
    p.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return p


def _read(user: Path) -> dict:
    return json.loads((user / "ccs-config.json").read_text(encoding="utf-8-sig"))


def _hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


#: A config as an older ccs would have written it -- the real aktuldjr shape.
OLD = {"on_divergence": "prompt", "difftool": None, "interactive": True,
       "status_detail": "auto", "status_max_lines": 30, "fetch": True}


# -- the five states ----------------------------------------------------------

def test_an_absent_file_is_created_with_every_key(tmp_path, capsys):
    user = tmp_path / "user"
    user.mkdir()
    rc = main(_ccs(user))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert set(_read(user)) == set(userconfig.KEYS)


def test_a_stale_file_gains_EXACTLY_the_missing_keys(tmp_path, capsys):
    user = tmp_path / "user"
    _write(user, OLD)
    main(_ccs(user))
    after = _read(user)
    assert set(after) == set(userconfig.KEYS)
    for key in OLD:
        assert after[key] == OLD[key], f"{key} was changed"


def test_a_current_file_is_not_written_at_all(tmp_path, capsys):
    """Not merely 'unchanged' -- untouched. Compare bytes, not contents."""
    user = tmp_path / "user"
    p = _write(user, dict(userconfig.DEFAULTS))
    before = _hash(p)
    rc = main(_ccs(user))
    out = capsys.readouterr().out
    assert rc == 0
    assert _hash(p) == before, "a no-op run rewrote the file"
    assert "current" in out


def test_a_value_the_user_set_TO_THE_DEFAULT_is_not_rewritten(tmp_path, capsys):
    """Provenance, not equality -- the subtle half of additive-only.

    A key holding the default value is indistinguishable from one ccs wrote.
    Both must be left alone; "it equals the default so rewriting is harmless"
    is the reasoning that eventually reformats somebody's whole file.
    """
    user = tmp_path / "user"
    body = dict(OLD)
    body["fetch"] = userconfig.DEFAULTS["fetch"]      # deliberately the default
    p = _write(user, body)
    raw_before = p.read_text(encoding="utf-8")
    main(_ccs(user))
    raw_after = p.read_text(encoding="utf-8")
    assert '"fetch": true' in raw_after
    # everything that was there before is still there, character for character
    for line in raw_before.splitlines():
        if line.strip().rstrip(",") and ":" in line:
            assert line.rstrip(",") in raw_after, f"lost or reformatted: {line!r}"


def test_a_user_value_that_DIFFERS_from_the_default_survives(tmp_path, capsys):
    user = tmp_path / "user"
    body = dict(OLD)
    body["status_max_lines"] = 999
    _write(user, body)
    main(_ccs(user))
    assert _read(user)["status_max_lines"] == 999


def test_an_unknown_key_is_reported_and_kept(tmp_path, capsys):
    """Never removed: it usually means a NEWER ccs wrote this file."""
    user = tmp_path / "user"
    body = dict(OLD)
    body["from_a_future_version"] = "keep me"
    _write(user, body)
    rc = main(_ccs(user))
    out = capsys.readouterr().out
    assert "unknown key" in out and "from_a_future_version" in out
    assert _read(user)["from_a_future_version"] == "keep me", out
    assert rc == 0, "an unknown key is a complete outcome, not a failure"


# -- refusing to act ----------------------------------------------------------

def test_an_unparseable_file_is_never_written_over(tmp_path, capsys):
    """It may hold settings the user cares about; rewriting destroys them."""
    user = tmp_path / "user"
    user.mkdir()
    p = user / "ccs-config.json"
    p.write_text('{ "on_divergence": "prompt",,, ', encoding="utf-8")
    before = _hash(p)
    rc = main(_ccs(user))
    cap = capsys.readouterr()
    assert rc != 0, cap.out
    assert _hash(p) == before, "an unreadable config was overwritten"
    assert "cannot update" in cap.out
    # The remedy goes to STDERR, matching csb: status on stdout, what-to-run
    # on stderr, so piping the status does not swallow the instruction.
    assert "by hand" in cap.err or "move it aside" in cap.err, cap.err


def test_a_file_that_is_not_an_object_is_refused(tmp_path, capsys):
    user = tmp_path / "user"
    user.mkdir()
    (user / "ccs-config.json").write_text("[1, 2, 3]", encoding="utf-8")
    rc = main(_ccs(user))
    assert rc != 0
    assert "cannot update" in capsys.readouterr().out


# -- the plan is computed once, and both paths read it ------------------------

def test_the_plan_reports_missing_without_touching_present_values(tmp_path):
    user = tmp_path / "user"
    _write(user, OLD)
    plan = userconfig.plan_config(user)
    assert plan.exists
    assert set(plan.missing) == set(userconfig.KEYS) - set(OLD)
    assert plan.would_write
    assert not plan.needs_a_human


def test_the_plan_for_an_absent_file_offers_every_key(tmp_path):
    plan = userconfig.plan_config(tmp_path / "nothing-here")
    assert not plan.exists
    assert set(plan.missing) == set(userconfig.KEYS)


def test_an_unknown_key_does_not_count_as_needing_a_human(tmp_path):
    """Reported and left alone IS the complete, correct outcome."""
    user = tmp_path / "user"
    _write(user, {**OLD, "mystery": 1})
    plan = userconfig.plan_config(user)
    assert plan.unknown == ["mystery"]
    assert not plan.needs_a_human


def test_underscore_keys_are_not_reported_as_unknown(tmp_path):
    """`_comment` is how the sibling records explain themselves to a reader."""
    user = tmp_path / "user"
    _write(user, {**OLD, "_comment": "notes to myself"})
    assert userconfig.plan_config(user).unknown == []


def test_applying_a_plan_over_an_unreadable_file_raises(tmp_path):
    user = tmp_path / "user"
    user.mkdir()
    (user / "ccs-config.json").write_text("{oops", encoding="utf-8")
    plan = userconfig.plan_config(user)
    with pytest.raises(ValueError):
        userconfig.apply_config_plan(plan)


def test_your_keys_keep_their_POSITION_and_new_ones_are_appended(tmp_path):
    """The guarantee is extend-in-place, not regenerate-and-overlay.

    Found by a red-green audit: replacing the implementation with
    `dict(DEFAULTS)` then `.update(existing)` produced identical CONTENT, so
    every content-based test still passed. The observable difference is ORDER
    -- regeneration reorders the file a user arranged by hand, turning a
    5-line addition into a whole-file diff. Content tests cannot see that;
    this one can.
    """
    user = tmp_path / "user"
    _write(user, OLD)
    main(_ccs(user))
    after = list(_read(user))
    assert after[:len(OLD)] == list(OLD), (
        "existing keys moved; they must keep their positions")
    assert set(after[len(OLD):]) == set(userconfig.KEYS) - set(OLD), (
        "new keys must be appended after what was already there")


def test_an_unknown_key_keeps_its_position_too(tmp_path):
    """A key from a newer ccs must not be shuffled to the end either."""
    user = tmp_path / "user"
    body = {"on_divergence": "prompt", "from_the_future": 1, "fetch": True}
    _write(user, body)
    main(_ccs(user))
    after = list(_read(user))
    assert after[:3] == ["on_divergence", "from_the_future", "fetch"]


def test_applying_a_plan_over_a_file_that_PARSES_but_is_not_an_object_raises():
    """The second unreadable shape, which the first test could not distinguish.

    Caught by mutation M4: deleting the explicit guard in apply_config_plan
    still satisfied `pytest.raises(ValueError)` for BROKEN json, because
    json.loads then raises JSONDecodeError -- itself a ValueError. The test
    passed for the wrong reason.

    A file holding `[1, 2, 3]` parses cleanly, so no decode error rescues it;
    without the guard it reaches `.update()` on a list. The guard is
    load-bearing and this is what proves it.
    """
    import tempfile
    from pathlib import Path as _P
    d = _P(tempfile.mkdtemp())
    (d / "ccs-config.json").write_text("[1, 2, 3]", encoding="utf-8")
    plan = userconfig.plan_config(d)
    assert plan.unreadable == "the file is not a JSON object"
    with pytest.raises(ValueError) as excinfo:
        userconfig.apply_config_plan(plan)
    # ...and specifically OUR refusal, not an incidental decode error
    assert "refusing to write over" in str(excinfo.value)


def test_the_refusal_names_our_reason_not_an_incidental_decode_error():
    """Same lesson for the broken-JSON path: assert WHICH error, not just that
    one occurred. `pytest.raises(ValueError)` is satisfied by JSONDecodeError,
    so it cannot tell a deliberate refusal from an accidental crash."""
    import tempfile
    from pathlib import Path as _P
    d = _P(tempfile.mkdtemp())
    (d / "ccs-config.json").write_text("{oops", encoding="utf-8")
    plan = userconfig.plan_config(d)
    with pytest.raises(ValueError) as excinfo:
        userconfig.apply_config_plan(plan)
    assert "refusing to write over" in str(excinfo.value)


# -- --dry-run ----------------------------------------------------------------

def test_dry_run_writes_nothing(tmp_path, capsys):
    """Verified by hashing the file, not by reading its contents back."""
    user = tmp_path / "user"
    p = _write(user, OLD)
    before = _hash(p)
    rc = main(_ccs(user, "--dry-run"))
    out = capsys.readouterr().out
    assert rc == 0
    assert _hash(p) == before, "--dry-run wrote to the file"
    assert "nothing was written" in out


def test_dry_run_names_every_key_it_would_add_with_its_default(tmp_path, capsys):
    user = tmp_path / "user"
    _write(user, OLD)
    main(_ccs(user, "--dry-run"))
    out = capsys.readouterr().out
    for key in set(userconfig.KEYS) - set(OLD):
        assert key in out, f"{key} not named in the dry run"
    assert '"untouched"' in out          # the default, as it will be written
    assert "15" in out


def test_dry_run_on_an_absent_file_says_it_would_create_one(tmp_path, capsys):
    user = tmp_path / "user"
    user.mkdir()
    rc = main(_ccs(user, "--dry-run"))
    out = capsys.readouterr().out
    assert rc == 0
    assert not (user / "ccs-config.json").exists(), "--dry-run created the file"
    assert "would create" in out


def test_dry_run_and_the_real_run_agree_on_what_is_missing(tmp_path, capsys):
    """Both read one plan, so the preview cannot disagree with the action."""
    user = tmp_path / "user"
    _write(user, OLD)
    main(_ccs(user, "--dry-run"))
    previewed = {ln.split()[2] for ln in capsys.readouterr().out.splitlines()
                 if ln.strip().startswith("would add")}
    main(_ccs(user))
    added = {ln.split()[1] for ln in capsys.readouterr().out.splitlines()
             if ln.strip().startswith("added")}
    assert previewed == added, "the preview and the action disagreed"


def test_dry_run_says_the_write_changes_no_behaviour(tmp_path, capsys):
    """Every value written is already in effect, and the output says so.

    From a real first run on an unconfigured machine: eleven settings listed
    as about to be written, with nothing saying whether that ALTERS anything.
    It does not -- each value is the setting already in force. The case that
    matters most is the update path, where a setting governing whether files
    get moved appears in your config and could read as switching it on.
    """
    user = tmp_path / "user"
    user.mkdir()
    main(_ccs(user, "--dry-run"))
    assert "changes nothing ccs does" in capsys.readouterr().out


def test_the_same_reassurance_appears_when_updating(tmp_path, capsys):
    user = tmp_path / "user"
    _write(user, OLD)
    main(_ccs(user, "--dry-run"))
    out = capsys.readouterr().out
    assert "sync_removals" in out                    # the file-moving setting
    assert "changes nothing ccs does" in out


def test_the_header_reads_as_english(tmp_path, capsys):
    """`would be creating` -- caught by a human running it, not by a test."""
    user = tmp_path / "user"
    user.mkdir()
    main(_ccs(user, "--dry-run"))
    out = capsys.readouterr().out
    assert "would create" in out
    assert "would be creating" not in out


def test_the_count_is_pluralised_properly(tmp_path, capsys):
    """`1 setting(s)` is the kind of thing nobody tests and everybody sees."""
    user = tmp_path / "user"
    body = dict(userconfig.DEFAULTS)
    body.pop("sync_removals")                        # exactly one missing
    _write(user, body)
    main(_ccs(user, "--dry-run"))
    out = capsys.readouterr().out
    assert "1 setting would" in out
    assert "setting(s)" not in out


# -- --explain ----------------------------------------------------------------

def test_explain_one_setting(tmp_path, capsys):
    user = tmp_path / "user"
    user.mkdir()
    rc = main(_ccs(user, "--explain", "fetch"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "fetch" in out
    assert "CCS_FETCH" in out                       # names the env var
    assert "default:" in out
    assert userconfig.KEYS["fetch"].explain.split(".")[0] in out.replace("\n  ", " ")


def test_explain_shows_the_valid_values_for_an_enum(tmp_path, capsys):
    user = tmp_path / "user"
    user.mkdir()
    main(_ccs(user, "--explain", "sync_removals"))
    out = capsys.readouterr().out
    for choice in userconfig.KEYS["sync_removals"].choices:
        assert choice in out


def test_explain_with_no_key_covers_every_setting(tmp_path, capsys):
    user = tmp_path / "user"
    user.mkdir()
    rc = main(_ccs(user, "--explain"))
    out = capsys.readouterr().out
    assert rc == 0
    for name in userconfig.KEYS:
        assert name in out, f"{name} missing from --explain"


def test_explain_a_setting_that_does_not_exist(tmp_path, capsys):
    user = tmp_path / "user"
    user.mkdir()
    rc = main(_ccs(user, "--explain", "nonsense"))
    out = capsys.readouterr().out
    assert rc != 0
    assert "no such setting" in out
    assert "sync_removals" in out                   # lists what IS known


def test_explain_writes_nothing(tmp_path, capsys):
    """It is a question, not a command."""
    user = tmp_path / "user"
    user.mkdir()
    main(_ccs(user, "--explain"))
    assert not (user / "ccs-config.json").exists()


def test_explain_needs_no_config_file_to_work(tmp_path, capsys):
    """A new machine should be able to ask before it has anything."""
    user = tmp_path / "user"
    user.mkdir()
    assert main(_ccs(user, "--explain", "auto_pull")) == 0


def test_the_command_the_tool_ADVERTISES_actually_exists(tmp_path, capsys):
    """After a real write the output names `--explain <key>`. It must work.

    It did not, when a real run first produced that line -- the tool was
    advertising a command it did not have, in a file it had just written to
    someone's machine.
    """
    user = tmp_path / "user"
    user.mkdir()
    main(_ccs(user))
    advertised = capsys.readouterr().out
    assert "--explain <key>" in advertised
    assert main(_ccs(user, "--explain", "fetch")) == 0


# -- wording caught by real runs ----------------------------------------------

def test_the_real_write_pluralises_properly_too(tmp_path, capsys):
    """The dry-run path was fixed and the write path was not -- a half-fix."""
    user = tmp_path / "user"
    body = dict(userconfig.DEFAULTS)
    body.pop("sync_removals")
    _write(user, body)
    main(_ccs(user))
    out = capsys.readouterr().out
    assert "1 setting," in out or "1 setting " in out
    assert "setting(s)" not in out


def test_the_real_write_repeats_the_reassurance(tmp_path, capsys):
    """It matters MORE after a write than before one."""
    user = tmp_path / "user"
    _write(user, OLD)
    main(_ccs(user))
    assert "changed nothing ccs does" in capsys.readouterr().out


def test_creating_a_file_does_not_claim_your_settings_were_preserved(tmp_path, capsys):
    """'nothing you had set was changed' is vacuous with no prior file."""
    user = tmp_path / "user"
    user.mkdir()
    main(_ccs(user))
    out = capsys.readouterr().out
    assert "nothing you had set was changed" not in out
    assert "every one at its default" in out
