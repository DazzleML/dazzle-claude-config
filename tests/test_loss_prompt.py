"""The no-base loss prompt, and the width helper it reads through.

The maintainer met the prompt on a real merge (2026-09-03) and could not tell
what it wanted: a sentence trailing into `--`, "ours" for his own live file,
"(1)" for a count, a line cut at 100 characters with nothing to say so, and a
question that never said what `y` or `N` would do. Until then the prompt had
one test -- the non-interactive refusal -- and no person had seen it rendered.
These pin the words, the count, the cut marker and its absence, and the two
sentences that say what each answer does.
"""
from __future__ import annotations

import builtins
from types import SimpleNamespace

import pytest

from dazzle_claude_config import merge, render


# -- render.fit: the dz list idiom ---------------------------------------------

def test_fit_leaves_a_line_that_fits_alone(monkeypatch):
    monkeypatch.setattr(render, "terminal_width", lambda: 80)
    assert render.fit("short", indent=4) == ("short", False)


def test_fit_cuts_to_the_real_budget_and_marks_it(monkeypatch):
    monkeypatch.setattr(render, "terminal_width", lambda: 40)
    shown, was_cut = render.fit("x" * 60, indent=4)
    assert was_cut and shown.endswith("...")
    assert len(shown) == 40 - 4             # indent counted; marker inside the budget


def test_fit_does_not_cut_when_there_is_no_room_to_say_so(monkeypatch):
    """A three-column budget would print only the marker; below min_width the
    line goes out whole and the terminal wraps it -- the lesser evil."""
    monkeypatch.setattr(render, "terminal_width", lambda: 20)
    assert render.fit("x" * 60, indent=4) == ("x" * 60, False)


def test_terminal_width_falls_back_to_80_when_piped(monkeypatch):
    monkeypatch.delenv("COLUMNS", raising=False)
    import shutil
    monkeypatch.setattr(shutil, "get_terminal_size", lambda fb: SimpleNamespace(columns=fb[0]))
    assert render.terminal_width() == 80


# -- the prompt, as a person reads it ----------------------------------------

LONG = ("4. Run `check-deps` and, once per project, `stack-check`, so each reuse "
        "row carries its dependency note and nothing is moved before its imports are known.")


def _ask(monkeypatch, capsys, lost, answer="n", width=200):
    monkeypatch.setattr(merge, "interactive", lambda: True)
    monkeypatch.setattr(render, "terminal_width", lambda: width)
    seen = {}

    def fake_input(prompt=""):
        seen["prompt"] = prompt
        return answer

    monkeypatch.setattr(builtins, "input", fake_input)
    item = SimpleNamespace(label="agents/refactor-advisor.md")
    result = merge._ask_loss_on_console(item, SimpleNamespace(lost=lost))
    out = capsys.readouterr().out + seen.get("prompt", "")
    return result, out


def test_the_prompt_says_what_it_knows_and_what_each_answer_does(monkeypatch, capsys):
    result, out = _ask(monkeypatch, capsys, {"ours": [LONG]})
    assert result is False
    assert out.startswith("agents/refactor-advisor.md: no common ancestor, so ccs cannot tell "
                          "a deliberate deletion from a lost line.")
    assert "The merged result drops 1 line that your live file has:" in out
    # the line is OURS, so the sentence names you, not the payload
    assert "If you dropped that line from your live file on purpose, install the result: y." in out
    assert "If it should have stayed, answer N and resolve the file by hand.  [y/N]" in out
    # the old words are gone
    for old in ("only in ours", "absent from your result", "by accident --",
                "you reviewed this file", "install it anyway"):
        assert old not in out, old


def test_a_wide_terminal_shows_the_line_whole_and_no_cut_note(monkeypatch, capsys):
    _, out = _ask(monkeypatch, capsys, {"ours": [LONG]}, width=200)
    assert LONG in out
    assert "cut to fit the terminal" not in out


def test_a_narrow_terminal_marks_the_cut_and_names_the_way_to_see_it_whole(monkeypatch, capsys):
    _, out = _ask(monkeypatch, capsys, {"ours": [LONG]}, width=60)
    assert LONG not in out
    assert "..." in out
    assert "(cut to fit the terminal; ccs diff agents/refactor-advisor.md shows them whole)" in out
    # no printed line exceeds the terminal
    assert all(len(line) <= 60 for line in out.splitlines() if line.startswith("    "))


def test_plural_and_both_sides_read_right(monkeypatch, capsys):
    """Mixed losses: the closing pair must not blame the payload for a line
    that was yours (the eyes-step finding of 2026-09-03)."""
    _, out = _ask(monkeypatch, capsys, {"ours": ["a", "b"], "theirs": ["c"]})
    assert "drops 2 lines that your live file has:" in out
    assert "drops 1 line that the payload's copy has:" in out
    assert ("If each of those lines was dropped on purpose -- the payload's by the payload, "
            "yours by you -- install the result: y.") in out
    assert "If any of them should have stayed, answer N" in out
    assert "If the payload removed" not in out


def test_a_payload_only_loss_names_the_payload(monkeypatch, capsys):
    _, out = _ask(monkeypatch, capsys, {"theirs": ["c", "d"]})
    assert "drops 2 lines that the payload's copy has:" in out
    assert "If the payload removed those lines on purpose, install the result: y." in out
    assert "If they should have stayed, answer N" in out


def test_the_failure_strings_use_the_same_words_and_mark_their_cut():
    """validate()'s failures print right after the prompt; they must speak the
    same language (no 'ours'/'theirs') and never cut a quote silently."""
    from dazzle_claude_config.merge import _excerpt, _side_name
    assert _side_name("ours") == "your live file" and _side_name("theirs") == "the payload's copy"
    assert _excerpt("short", 70) == "short"
    long = "x" * 100
    assert _excerpt(long, 70).endswith("...") and len(_excerpt(long, 70)) == 70


def test_more_than_twelve_lines_are_capped_with_a_count(monkeypatch, capsys):
    _, out = _ask(monkeypatch, capsys, {"ours": [f"line {i}" for i in range(15)]})
    assert "... and 3 more" in out


def test_yes_installs_and_the_default_does_not(monkeypatch, capsys):
    assert _ask(monkeypatch, capsys, {"ours": ["a"]}, answer="y")[0] is True
    assert _ask(monkeypatch, capsys, {"ours": ["a"]}, answer="")[0] is False


def test_colour_roles_when_colour_is_on(monkeypatch, capsys):
    """The family convention (render.py, after csb's search_render): the path
    bold cyan, the attention line yellow, the file content magenta, the note
    dim, the answer letters bold. Off by default in tests (no TTY), so the
    wording tests above see plain text; this one switches it on."""
    monkeypatch.setattr(render, "_enabled", True)
    _, out = _ask(monkeypatch, capsys, {"ours": [LONG]}, width=60)
    esc = render._ANSI
    assert esc["bold_cyan"] + "agents/refactor-advisor.md" + esc["reset"] in out
    assert esc["yellow"] + "The merged result drops 1 line" in out
    assert esc["magenta"] + "4. Run `check-deps`" in out
    assert esc["dim"] + "  (cut to fit the terminal; " in out
    assert esc["bold"] + "y" + esc["reset"] + "." in out
    assert esc["bold"] + "N" + esc["reset"] in out


def test_no_colour_when_piped(monkeypatch, capsys):
    monkeypatch.setattr(render, "_enabled", False)
    _, out = _ask(monkeypatch, capsys, {"ours": [LONG]})
    assert "\033[" not in out


def test_non_interactive_never_accepts(monkeypatch, capsys):
    monkeypatch.setattr(merge, "interactive", lambda: False)
    item = SimpleNamespace(label="x.md")
    assert merge._ask_loss_on_console(item, SimpleNamespace(lost={"ours": ["a"]})) is False
    assert capsys.readouterr().out == ""
