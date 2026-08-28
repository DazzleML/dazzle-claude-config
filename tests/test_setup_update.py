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

from dazzle_claude_config import cli, merge, userconfig
from dazzle_claude_config.cli import main


def _ccs(user: Path, *rest) -> list[str]:
    return ["--user-claude", str(user), "--no-color", "setup", "update", *rest]


def _refuse_to_prompt(prompt: str):
    """Stand-in for `input` where asking is itself the bug.

    A prompt in a redirected or non-answerable run does not fail loudly -- it
    HANGS, or silently eats an EOF and looks fine. Asserting on the output
    afterwards would miss both, so the assertion has to be here, at the moment
    the question is asked.
    """
    raise AssertionError(
        f"nothing should have been asked here, but ccs asked: {prompt!r}")


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
    # Pin the CANONICAL sentence, not a copy of its text. This asserted the
    # literal words, so improving the wording broke the test rather than the
    # test proving the wording is shared -- which is the same "two copies that
    # must agree" trap the constant exists to remove.
    assert plan.unreadable == userconfig.NOT_AN_OBJECT
    assert "not a JSON object" in plan.unreadable   # ...and it still says so
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


def _console(stdout: bool = True, stdin: bool = True):
    """Stand in for merge._console_attached, answering per stream.

    Two streams, two questions: can the reader SEE this (stdout), and can they
    ANSWER it (stdin). They come apart in ordinary use -- `ccs ... | less`
    reads from a terminal and writes to a pipe -- and the wrong answer to
    either is a command that hangs on a prompt nobody can see.
    """
    import sys as _sys

    def fake(stream):
        return stdout if stream is _sys.stdout else stdin
    return fake


def test_at_a_console_the_bare_form_is_an_INDEX_not_an_80_line_dump(
        tmp_path, capsys, monkeypatch):
    """The defect this replaced: every setting in full, 80 lines, at a prompt.

    That is a scroll, not a reference -- the top is gone before you have read
    the bottom, and there is nothing to do about it but run it again through a
    pager. The index fits on a screen and every setting is one command away.
    """
    monkeypatch.setattr(merge, "_console_attached", _console())
    monkeypatch.setattr("builtins.input", lambda _p: "n")
    user = tmp_path / "user"
    user.mkdir()
    rc = main(_ccs(user, "--explain"))
    out = capsys.readouterr().out

    assert rc == 0
    assert len(out.splitlines()) < 25, (
        f"the index should fit on a screen, got {len(out.splitlines())} lines")
    for name in userconfig.KEYS:
        assert name in out, f"{name} missing from the index"
    assert userconfig.DOCS_URL in out, "the index must name the full reference"
    assert "--explain <name>" in out, (
        "the index must teach the per-setting form -- nobody can ask for "
        "`--explain sync_removals` if nothing told them it exists")
    # The full text of the longest explanation is NOT here.
    assert "refuses to do quietly" not in out


def test_saying_yes_at_the_prompt_prints_everything(tmp_path, capsys,
                                                    monkeypatch):
    monkeypatch.setattr(merge, "_console_attached", _console())
    monkeypatch.setattr("builtins.input", lambda _p: "y")
    user = tmp_path / "user"
    user.mkdir()
    rc = main(_ccs(user, "--explain"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "refuses to do quietly" in out, "answering yes must print the text"
    assert "CCS_SYNC_REMOVALS" in out


def test_saying_no_at_the_prompt_prints_nothing_further(tmp_path, capsys,
                                                        monkeypatch):
    monkeypatch.setattr(merge, "_console_attached", _console())
    monkeypatch.setattr("builtins.input", lambda _p: "")   # bare Enter = No
    user = tmp_path / "user"
    user.mkdir()
    assert main(_ccs(user, "--explain")) == 0
    assert "refuses to do quietly" not in capsys.readouterr().out


def test_redirected_output_gets_EVERYTHING_and_is_never_prompted(
        tmp_path, capsys, monkeypatch):
    """Redirecting or piping is an explicit request for the content, and
    nothing scrolls away in a file. Prompting there is the real bug: measured
    on this machine, a piped run printed the index and then sat on a question
    the pipe could never answer.
    """
    monkeypatch.setattr(merge, "_console_attached", _console(stdout=False))
    monkeypatch.setattr("builtins.input", _refuse_to_prompt)
    user = tmp_path / "user"
    user.mkdir()
    rc = main(_ccs(user, "--explain"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "refuses to do quietly" in out
    assert userconfig.DOCS_URL not in out, (
        "a redirected run is not being read on a web page; give it the text")


def test_a_console_that_cannot_be_ASKED_shows_the_index_and_stops(
        tmp_path, capsys, monkeypatch):
    """`ccs ... | less` on some shells, and cron with a tty attached: output
    is visible, input is not answerable. Show, do not ask."""
    monkeypatch.setattr(merge, "_console_attached", _console(stdin=False))
    monkeypatch.setattr("builtins.input", _refuse_to_prompt)
    user = tmp_path / "user"
    user.mkdir()
    assert main(_ccs(user, "--explain")) == 0
    assert userconfig.DOCS_URL in capsys.readouterr().out


def test_CCS_INTERACTIVE_off_suppresses_the_prompt(tmp_path, capsys,
                                                   monkeypatch):
    """Read from the environment alone, never from the config file: --explain
    must keep working on a machine that has no config yet."""
    monkeypatch.setattr(merge, "_console_attached", _console())
    monkeypatch.setattr("builtins.input", _refuse_to_prompt)
    monkeypatch.setenv("CCS_INTERACTIVE", "0")
    user = tmp_path / "user"
    user.mkdir()
    assert main(_ccs(user, "--explain")) == 0
    assert userconfig.DOCS_URL in capsys.readouterr().out


def test_ctrl_c_at_the_prompt_exits_cleanly(tmp_path, capsys, monkeypatch):
    """A question the reader declines is not an error."""
    def interrupt(_p):
        raise KeyboardInterrupt

    monkeypatch.setattr(merge, "_console_attached", _console())
    monkeypatch.setattr("builtins.input", interrupt)
    user = tmp_path / "user"
    user.mkdir()
    assert main(_ccs(user, "--explain")) == 0


def test_asking_about_ONE_setting_never_prompts_or_links(tmp_path, capsys,
                                                         monkeypatch):
    """The form that matters most stays exactly as it was: a direct answer,
    at a terminal, with no question attached and no page to visit."""
    monkeypatch.setattr(merge, "_console_attached", _console())
    monkeypatch.setattr("builtins.input", _refuse_to_prompt)
    user = tmp_path / "user"
    user.mkdir()
    rc = main(_ccs(user, "--explain", "sync_removals"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "refuses to do quietly" in out
    assert userconfig.DOCS_URL not in out


def test_the_index_uses_no_characters_cmd_exe_cannot_print(tmp_path, capsys,
                                                           monkeypatch):
    """Windows cmd.exe defaults to codepage 437. A single-character ellipsis
    comes out as a replacement glyph -- observed in the first run of this
    index, where every truncated line ended in one.
    """
    monkeypatch.setattr(merge, "_console_attached", _console())
    monkeypatch.setattr("builtins.input", lambda _p: "n")
    user = tmp_path / "user"
    user.mkdir()
    main(_ccs(user, "--explain"))
    out = capsys.readouterr().out
    out.encode("cp437")        # raises UnicodeEncodeError if it cannot print
    assert "..." in out, "long summaries should be truncated with ASCII dots"


# -- _gist, the index's one-line summaries -------------------------------------
#
# Every test below exists because a mutation survived the v0.5.12 sweep. `_gist`
# is a pure function and every assertion about it had been going through the
# rendered index, where the real explanations happen not to exercise any of its
# edges. Testing a pure function through its caller's output is how a whole
# function ends up unconstrained while looking covered.

def test_gist_keeps_a_summary_that_is_exactly_the_width_allowed():
    """Mutation M7: `<=` became `<`, and a summary of exactly `width`
    characters was truncated for no reason. Nothing failed, because no real
    explanation happens to land on the boundary."""
    assert cli._gist("abcdefghij", 10) == "abcdefghij"
    assert "..." not in cli._gist("abcdefghij", 10)
    assert cli._gist("abcdefghijk", 10).endswith("...")


def test_gist_trims_to_the_LAST_word_boundary_not_the_first():
    """Mutation M8: `rindex` became `index`, collapsing every truncated
    summary to a single word. The index still rendered, still fit, and still
    looked plausible -- which is exactly why nothing caught it."""
    got = cli._gist("alpha beta gamma delta", 15)
    assert got == "alpha beta...", (
        f"expected as many whole words as fit, got {got!r}")


def test_gist_ends_a_sentence_at_a_period_FOLLOWED_BY_A_SPACE():
    """Mutation M6: `split(". ")` became `split(".")`, so any explanation
    holding a version number, an abbreviation or a filename was cut at the
    first period. None of the current eleven contain one -- so the mutant
    survived on the real table and would have bitten the first setting whose
    explanation mentioned `ccs-config.json`."""
    got = cli._gist("Set this to v1.2 before you start. Then read on.", 80)
    assert "v1.2" in got, f"truncated inside a version number: {got!r}"
    assert "Then read on" not in got, "it must still stop at the first sentence"


def test_gist_handles_a_first_word_longer_than_the_whole_budget():
    """No mutation for this one -- it is the edge the others made me look at.
    A single unbreakable token must not produce an empty summary or crash."""
    got = cli._gist("supercalifragilisticexpialidocious rest", 10)
    assert got and got.endswith("...")


def test_CCS_INTERACTIVE_is_honoured_with_stray_whitespace(tmp_path, capsys,
                                                           monkeypatch):
    """Mutation M11 dropped `.strip()` from the environment read and survived.

    Whitespace around an environment variable is not exotic: it is what a
    shell script writing `CCS_INTERACTIVE="0 "` or a CI system templating a
    value produces. Without the strip, the opt-out silently stops working and
    the run stops on a prompt -- the failure this whole branch exists to
    prevent.
    """
    monkeypatch.setattr(merge, "_console_attached", _console())
    monkeypatch.setattr("builtins.input", _refuse_to_prompt)
    monkeypatch.setenv("CCS_INTERACTIVE", "  0  ")
    user = tmp_path / "user"
    user.mkdir()
    assert main(_ccs(user, "--explain")) == 0


def test_an_empty_setting_name_is_an_ERROR_not_a_fall_through():
    """Mutation M9 turned `only is not None` into `only`, so an empty setting
    name skipped the unknown-setting error and quietly showed the index.

    The CLI normalises "" to None before this point, so the mutant is
    unreachable through argparse today -- but the guard is what makes that
    normalisation safe to change later, and a guard nothing tests is a guard
    that gets 'simplified' by the next person to read it.
    """
    assert cli._explain_settings("") == cli.EXIT_ERROR


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
