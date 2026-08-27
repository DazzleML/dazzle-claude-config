"""The explanations are packaged DATA, and that has to keep being true.

The prose that `ccs setup update --explain` prints lives in
`dazzle_claude_config/settings-explanations.json`, not in the Python source.
That buys three things -- the wording can be edited without touching code, the
docs page can be generated from it, and a translation is a sibling file -- and
costs one: a data file only ships if `pyproject.toml` says so.

That cost is the dangerous kind, because it is INVISIBLE where the work is
done. In a checkout the file is on disk and everything works; after a
`pip install` with the packaging line missing, `--explain` comes up empty. The
only place it fails is a machine somebody installed the tool on -- which for
this project means the VPS the whole exercise exists to reach.

So the guarantees tested here are, in order of what they catch:

  * the file is reachable through `importlib.resources` -- the same mechanism
    the installed package uses, not `Path(__file__).parent`, which would pass
    in a checkout and prove nothing about a wheel
  * `pyproject.toml` actually declares it as package data
  * every failure to load it DEGRADES -- ccs still runs, still has its
    defaults, and says why the words are missing
  * the generated docs page is current

The wheel itself is checked in CI, where a build exists to look inside.
"""
from __future__ import annotations

import importlib.resources
import json
import subprocess
import sys
from pathlib import Path

import pytest

from dazzle_claude_config import userconfig

REPO = Path(__file__).resolve().parents[1]


# -- it ships -----------------------------------------------------------------

def test_the_explanations_are_reachable_the_way_an_install_reaches_them():
    """`importlib.resources`, not a path relative to __file__.

    A `__file__`-relative read passes in a checkout whether or not the file is
    packaged, so it would give exactly the false confidence this module exists
    to remove.
    """
    raw = (importlib.resources.files("dazzle_claude_config")
           .joinpath(userconfig.EXPLANATIONS_FILE).read_text(encoding="utf-8"))
    body = json.loads(raw)
    assert isinstance(body.get("settings"), dict)
    assert body["settings"], "the settings object is empty"


def test_pyproject_declares_the_file_as_package_data():
    """`[tool.setuptools.packages.find]` collects MODULES. A .json beside them
    needs its own declaration, and without it the file silently does not ship.
    """
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.setuptools.package-data]" in text, (
        "pyproject.toml must declare package-data or the explanations do not "
        "ship -- --explain would work in a checkout and be empty after a "
        "pip install")
    assert userconfig.EXPLANATIONS_FILE in text


def test_the_real_package_loads_its_explanations_without_error():
    texts, err = userconfig.load_explanations()
    assert err is None, f"the shipped explanations did not load: {err}"
    assert len(texts) == len(userconfig.KEYS)


# -- it degrades, it never breaks ---------------------------------------------

def test_prose_can_vanish_without_taking_the_DEFAULTS_with_it():
    """The load-bearing invariant of the whole split.

    An unexplained setting is an annoyance. A setting with no default is a
    machine quietly behaving differently from the one next to it. So the JSON
    holds words only, and losing it must cost words only.
    """
    rebuilt = userconfig._attach_explanations({
        "sync_removals": userconfig.Key(
            default="untouched", env="CCS_SYNC_REMOVALS",
            choices=frozenset({"untouched", "all", "never"})),
    })
    k = rebuilt["sync_removals"]
    assert k.default == "untouched", "the default must not live in the JSON"
    assert k.env == "CCS_SYNC_REMOVALS"
    assert k.choices == frozenset({"untouched", "all", "never"})
    assert "RETIRED" in k.explain, "and the prose must actually be attached"


def test_a_setting_with_no_prose_gets_an_EMPTY_explanation(monkeypatch):
    """Written because mutation M5 survived, and it survived by repeating the
    exact lesson of mutation L4 one release earlier.

    M5 changed the fallback from `EXPLANATIONS.get(name, "")` to
    `EXPLANATIONS.get(name, name)`, so a setting with no prose would carry its
    own NAME as its explanation. Nothing failed. The test above proved the
    defaults survive a missing explanation and never asked what the
    explanation itself became -- the same shape as L4, where proving the
    current keys were explained said nothing about the next one.

    Why the value matters and is not cosmetic: `_explain_one` branches on
    `if k.explain:`, so a truthy fallback silently suppresses the
    "explanation unavailable, see the docs page" path. A packaging slip would
    then print each setting's own name back at the reader as its meaning.
    """
    monkeypatch.setattr(userconfig, "EXPLANATIONS", {})
    rebuilt = userconfig._attach_explanations({
        "orphan_setting": userconfig.Key(default=1, env="CCS_ORPHAN"),
    })
    explain = rebuilt["orphan_setting"].explain
    assert explain == "", (
        f"a setting with no prose must carry an EMPTY explanation, got "
        f"{explain!r} -- anything truthy makes `--explain` look answered")
    assert explain != "orphan_setting", "never fall back to the setting's name"


def test_a_missing_file_degrades_and_says_why(monkeypatch):
    monkeypatch.setattr(userconfig, "EXPLANATIONS_FILE", "no-such-file.json")
    texts, err = userconfig.load_explanations()
    assert texts == {}
    assert err and "not installed" in err


@pytest.mark.parametrize("raw,expected", [
    ("{not json at all", "not valid JSON"),
    ("[1, 2, 3]", "no 'settings' object"),
    ('{"settings": "a string, not an object"}', "no 'settings' object"),
    ('{"_comment": "no settings key here"}', "no 'settings' object"),
])
def test_a_malformed_file_degrades_and_says_why(monkeypatch, raw, expected):
    """Four shapes of broken, one behaviour: empty texts plus a reason. None
    of them may raise -- an unparseable prose file must not stop ccs running.
    """
    class _Fake:
        def joinpath(self, _name):
            return self

        def read_text(self, encoding=None):
            return raw

    monkeypatch.setattr(importlib.resources, "files", lambda _pkg: _Fake())
    texts, err = userconfig.load_explanations()
    assert texts == {}
    assert err and expected in err


def test_a_non_string_explanation_is_dropped_rather_than_trusted(monkeypatch):
    """A hand-edited file can hold anything. A list where prose belongs must
    not reach the formatter, which would render it as a Python repr."""
    class _Fake:
        def joinpath(self, _name):
            return self

        def read_text(self, encoding=None):
            return json.dumps({"settings": {"good": "text", "bad": ["list"]}})

    monkeypatch.setattr(importlib.resources, "files", lambda _pkg: _Fake())
    texts, err = userconfig.load_explanations()
    assert err is None
    assert texts == {"good": "text"}


# -- the generated page stays current -----------------------------------------

def test_the_docs_page_matches_the_settings_it_documents():
    """`docs/configuration.md` is generated, so it cannot describe a ccs that
    never shipped -- which is the drift that got a hand-written page rejected.
    This is the check that makes "generated" a guarantee instead of a habit:
    edit the JSON without regenerating and the suite goes red, in CI too.
    """
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "gen-config-docs.py"), "--check"],
        capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, (
        f"docs/configuration.md is out of date -- run "
        f"`python scripts/gen-config-docs.py`\n{r.stdout}{r.stderr}")


def test_the_docs_page_names_every_setting():
    page = (REPO / "docs" / "configuration.md").read_text(encoding="utf-8")
    for name in userconfig.KEYS:
        assert f"### {name}" in page, f"{name} has no section on the page"


def test_the_docs_url_points_at_the_page_that_exists():
    """A dead link in the tool's own output is worse than no link: it is a
    promise the reader follows and finds nothing at."""
    assert userconfig.DOCS_URL.endswith("/docs/configuration.md")
    assert (REPO / "docs" / "configuration.md").exists()
