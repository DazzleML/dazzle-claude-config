"""Every setting explains itself, and nothing can be added without one (#32).

The explanations used to be comments above each entry in `DEFAULTS`. Comments
are invisible to the program, so "what does this key mean?" could only be
answered by reading source. Every proposal for fixing that -- a docs page, a
generated sidecar, a parallel dict -- was a COPY, and a copy drifts: a key
gains a paragraph in source and the copy still describes the old behaviour.
`sync_removals` is the proof it happens; it was added on 2026-08-26, so any
documentation written the day before was already wrong about it.

So the explanations became data, and these tests are what make that stick.
The important one is `test_every_key_explains_itself`: without it the table is
just a nicer place to put comments, and the next key can still ship
unexplained. With it, that is a red suite rather than a silent gap.
"""
from __future__ import annotations

from dazzle_claude_config import userconfig


def test_every_key_explains_itself():
    """The anti-drift guarantee. Adding a key without an explanation is red."""
    missing = [name for name, k in userconfig.KEYS.items()
               if not (k.explain or "").strip()]
    assert not missing, f"settings with no explanation: {missing}"


def test_every_key_has_an_environment_variable():
    missing = [name for name, k in userconfig.KEYS.items()
               if not (k.env or "").strip()]
    assert not missing, f"settings with no env var: {missing}"


def test_environment_variables_follow_the_ccs_convention():
    wrong = {name: k.env for name, k in userconfig.KEYS.items()
             if k.env != f"CCS_{name.upper()}"}
    assert not wrong, f"env vars that do not match CCS_<KEY>: {wrong}"


def test_environment_variables_are_unique():
    """Two keys sharing an env var means one silently shadows the other."""
    seen: dict[str, str] = {}
    clashes = []
    for name, k in userconfig.KEYS.items():
        if k.env in seen:
            clashes.append((seen[k.env], name, k.env))
        seen[k.env] = name
    assert not clashes, f"env var collisions: {clashes}"


# -- the derived views must stay faithful -------------------------------------

def test_defaults_is_derived_from_the_table():
    assert userconfig.DEFAULTS == {n: k.default for n, k in userconfig.KEYS.items()}


def test_env_map_is_derived_from_the_table():
    assert userconfig.ENV_MAP == {n: k.env for n, k in userconfig.KEYS.items()}


def test_the_derived_views_cover_exactly_the_table():
    """No key may exist in one view and not the others."""
    assert set(userconfig.DEFAULTS) == set(userconfig.KEYS)
    assert set(userconfig.ENV_MAP) == set(userconfig.KEYS)


# -- choices ------------------------------------------------------------------

def test_a_keys_default_is_one_of_its_own_choices():
    """A default outside its own valid set would be unreachable by config.

    It would also make the load-time validator reject the built-in value on a
    machine with no config file at all.
    """
    bad = [(name, k.default, sorted(k.choices)) for name, k in userconfig.KEYS.items()
           if k.choices is not None and k.default not in k.choices]
    assert not bad, f"defaults outside their own choices: {bad}"


def test_the_valid_sets_are_the_tables_choices_not_copies():
    """The VALID_* names must be the same objects the table holds.

    They were independent literals before. Two sources for one fact is how a
    value gets added to the validator and not to the explanation, or vice
    versa -- the exact drift this unit exists to remove.
    """
    assert userconfig.VALID_ON_DIVERGENCE is userconfig.KEYS["on_divergence"].choices
    assert userconfig.VALID_STATUS_DETAIL is userconfig.KEYS["status_detail"].choices
    assert userconfig.VALID_SYNC_REMOVALS is userconfig.KEYS["sync_removals"].choices


def test_an_explanation_that_lists_choices_lists_all_of_them():
    """If the prose names the alternatives, it must not name a stale subset."""
    for name, k in userconfig.KEYS.items():
        if k.choices is None:
            continue
        quoted = sum(1 for c in k.choices if f"'{c}'" in k.explain)
        if quoted:      # the explanation is enumerating them
            assert quoted == len(k.choices), (
                f"{name}: explanation quotes {quoted} of {len(k.choices)} choices")


def test_explain_is_a_REQUIRED_field_not_an_optional_one():
    """The anti-drift promise is structural, not a habit.

    Caught by mutation L4: giving `explain` a default of "" left every current
    key explained, so the "every key explains itself" test still passed -- but
    the NEXT key could be added with no explanation and nothing would notice.
    Proving the keys are explained is weaker than proving they must be.
    """
    import dataclasses
    explain = next(f for f in dataclasses.fields(userconfig.Key)
                   if f.name == "explain")
    assert explain.default is dataclasses.MISSING, (
        "Key.explain must have no default -- an optional explanation lets a "
        "setting ship unexplained, which is what this table exists to prevent")
    assert explain.default_factory is dataclasses.MISSING


def test_a_choice_named_in_the_explanation_is_a_real_choice():
    """The other direction of the choices check.

    Caught by mutation L5: dropping a value from `choices` left the
    explanation still naming it, and the existing check only looked for
    choices missing FROM the prose -- never for prose naming a value the
    validator would reject. A user reading "'never' only reports" and then
    being told 'never' is not one of the valid options is the tool
    contradicting its own documentation.
    """
    for name, k in userconfig.KEYS.items():
        if k.choices is None:
            continue
        quoted = {w.strip("'") for w in k.explain.split() if w.startswith("'")}
        quoted = {q.rstrip(".,;:") for q in quoted}
        named_but_invalid = {q for q in quoted
                             if q and q.islower() and q not in k.choices
                             and q in _PLAUSIBLE_VALUES}
        assert not named_but_invalid, (
            f"{name}: explanation names {sorted(named_but_invalid)}, which "
            f"is not in choices {sorted(k.choices)}")


#: Values that look like settings rather than ordinary quoted prose. Keeps the
#: check above from flagging a quoted English word.
_PLAUSIBLE_VALUES = {
    "prompt", "skip", "force", "auto", "long", "compact",
    "untouched", "all", "never",
}
