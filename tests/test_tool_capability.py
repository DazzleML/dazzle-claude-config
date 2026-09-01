"""i1a -- the declared resume capability of merge tools.

Half A of the resume problem (2026-08-31 merge design): whether reopening a
tool over an EXISTING $MERGED preserves it is a fact about the tool, and ccs
never had it written down -- vimdiff resumes, meld does not, and nothing said
so. These tests pin the table's shape and the anti-drift guarantee that every
built-in declares its capability, the same shape as the settings table's
"every key has an explanation" guard.
"""
from dazzle_claude_config import merge


def test_every_builtin_tool_declares_a_resume_capability():
    """A new BUILTIN_TOOLS entry without a capability fails HERE, at the
    table, not on a person's resolved file."""
    # Checked against the NAME table, not through tool_resume(): the
    # executable fallback would classify a built-in whose name entry went
    # missing (meld's command runs `meld`), and the guard would go soft
    # without anyone noticing -- which is exactly what happened the first
    # time this file ran after the fallback was added.
    for name in merge.BUILTIN_TOOLS:
        assert name in merge.TOOL_RESUME, (
            f"built-in tool {name!r} has no entry in TOOL_RESUME")


def test_the_anti_drift_guard_actually_bites(monkeypatch):
    """Proving the current table happens to be complete is weak; proving a
    hole gets CAUGHT is the guarantee (the settings-table lesson, L4)."""
    monkeypatch.delitem(merge.TOOL_RESUME, "meld")
    holes = [n for n in merge.BUILTIN_TOOLS if n not in merge.TOOL_RESUME]
    assert holes == ["meld"]
    # and the fallback still answers for it -- the guard and the fallback are
    # different guarantees, and this pins that they stay different
    assert merge.tool_resume("meld") == merge.RESUME_WRITES_ONLY


def test_every_declared_capability_is_a_known_tier():
    for name, cap in merge.TOOL_RESUME.items():
        assert cap in merge.RESUME_TIERS or cap.startswith(merge.RESUME_INJECT_PREFIX), (
            f"{name!r} declares {cap!r}, which is not a tier")


def test_vimdiff_preloads_and_meld_and_kdiff3_do_not():
    assert merge.tool_resume("vimdiff") == merge.RESUME_PRELOADS
    assert merge.tool_resume("nvimdiff") == merge.RESUME_PRELOADS
    assert merge.tool_resume("meld") == merge.RESUME_WRITES_ONLY
    assert merge.tool_resume("kdiff3") == merge.RESUME_WRITES_ONLY


def test_beyondcompare_is_writes_only_under_gits_own_name():
    """`bc` is the only bc-* name git ships (measured: `git --exec-path`/mergetools
    holds `bc` alone). Every other name is a user's, classified by executable."""
    assert merge.tool_resume("bc") == merge.RESUME_WRITES_ONLY
    assert merge.reopen_is_safe("bc") is False


def _configured(monkeypatch, name: str, cmd: str):
    """A user's mergetool.<name>.cmd, and nothing else in git config."""
    monkeypatch.setattr(merge, "_git",
                        lambda args, cwd=None: (0, cmd)
                        if args[-1] == f"mergetool.{name}.cmd" else (1, ""))


def test_a_configured_tool_is_classified_by_its_executable_not_its_name(monkeypatch):
    """The maintainer's own config: `bc` points at BC5's BComp.exe for merge
    and BC4's BCompare.exe for diff -- one name, two versions -- so the name
    can never be the key. `bc5`, `beyond`, anything: the binary decides."""
    for name, cmd in (
        ("bc2", '"C:/Program Files/Beyond Compare 2/BComp.exe" $LOCAL $REMOTE $BASE $MERGED'),
        ("bc4", '"C:/Program Files/Beyond Compare 4/BComp.exe" "$LOCAL" "$REMOTE" "$BASE" "$MERGED"'),
        ("bc5", '"C:/Program Files/Beyond Compare 5/BComp.exe" "$LOCAL" "$REMOTE" "$BASE" "$MERGED"'),
        ("beyondcompare4", 'BCompare.exe $LOCAL $REMOTE $BASE $MERGED'),
        ("beyond", '/usr/bin/bcompare "$LOCAL" "$REMOTE" "$BASE" "$MERGED"'),
    ):
        _configured(monkeypatch, name, cmd)
        assert merge.tool_resume(name) == merge.RESUME_WRITES_ONLY, name
        assert merge.reopen_is_safe(name) is False, name
    _configured(monkeypatch, "myvim", 'vim -d "$LOCAL" "$BASE" "$REMOTE" "$MERGED"')
    assert merge.tool_resume("myvim") == merge.RESUME_PRELOADS
    assert merge.reopen_is_safe("myvim") is True


def test_the_executable_key_strips_path_case_and_extension():
    assert merge._exe_key('"C:\\Program Files\\Beyond Compare 5\\BComp.exe" a b') == "bcomp"
    assert merge._exe_key("/usr/bin/BCompare a b") == "bcompare"
    assert merge._exe_key("nvim -d x") == "nvim"
    assert merge._exe_key("") is None and merge._exe_key(None) is None


def test_an_unclassified_tool_is_the_floor_not_an_error(monkeypatch):
    """A tool from someone's mergetool.<name>.cmd that neither table has
    heard of still launches; only the reopen decision falls to the floor."""
    _configured(monkeypatch, "phantom", 'phantomtool "$LOCAL" "$MERGED"')
    assert merge.tool_resume("phantom") == merge.RESUME_UNKNOWN
    assert merge.reopen_is_safe("phantom") is False
    assert merge.inject_profile("phantom") is None
    monkeypatch.setattr(merge, "_git", lambda args, cwd=None: (1, ""))   # not configured at all
    assert merge.tool_resume("nothing") == merge.RESUME_UNKNOWN


def test_reopen_is_safe_only_for_preloads():
    assert merge.reopen_is_safe("vimdiff") is True
    assert merge.reopen_is_safe("meld") is False


def test_inject_profile_is_parsed_from_the_tier_string(monkeypatch):
    """No built-in declares inject yet (the driver has not shipped); the
    accessor's contract is pinned on a synthetic entry so the day it does,
    nothing about the parsing is new."""
    monkeypatch.setitem(merge.TOOL_RESUME, "synthetic", "inject:bc5")
    assert merge.inject_profile("synthetic") == "bc5"
    assert merge.reopen_is_safe("synthetic") is False   # inject is not preloads


# --- i1b: the registry as package data, with the settings-explanations contract

def test_the_packaged_registry_loads_and_names_no_error():
    reg, err = merge.load_tool_registry()
    assert err is None
    assert "tools" in reg and "inject_profiles" in reg
    assert merge.TOOL_REGISTRY_ERROR is None


def test_the_fallback_table_says_exactly_what_the_registry_says():
    """A packaging slip must downgrade ccs to 'no profiles', never to a
    DIFFERENT reopen decision -- so the two tables cannot disagree."""
    reg, _ = merge.load_tool_registry()
    shipped = {n: t["resume"] for n, t in reg["tools"].items()}
    assert shipped == merge._BUILTIN_RESUME


def test_the_bc5_profile_carries_the_fields_p1_confirmed():
    p = merge.INJECT_PROFILES["bc5"]
    assert p["os"] == "windows"
    assert p["window"]["class"] == "TViewForm"
    assert p["pane"]["class"] == "TTextEditor"
    assert p["pane"]["landmark"] == {"class": "TUiRadioButton", "text": "Other"}
    assert p["locate"] == "newly-appeared-visible-exactly-one"
    assert p["gate"] == "focus-handle-equals-resolved-pane"
    assert p["keys"] == ["^a", "^v", "^s"]
    assert p["exit_codes"]["101"] == "not-saved" and p["exit_codes"]["0"] == "saved"


def test_a_missing_registry_degrades_to_the_fallback_with_a_reason(monkeypatch):
    """Simulate the packaging slip: the resource is not there."""
    import importlib
    class _NoFiles:
        def files(self, pkg):
            raise ModuleNotFoundError("no package data")
    monkeypatch.setattr(importlib, "resources", _NoFiles())
    reg, err = merge.load_tool_registry()
    assert reg == {} and err and "not installed" in err
    assert merge._resume_table(reg) == merge._BUILTIN_RESUME


def _registry_from(monkeypatch, text: str):
    import importlib
    class _Res:
        def files(self, pkg):
            return self
        def joinpath(self, name):
            return self
        def read_text(self, encoding="utf-8"):
            return text
    monkeypatch.setattr(importlib, "resources", _Res())
    return merge.load_tool_registry()


def test_invalid_json_and_wrong_shapes_each_name_their_reason(monkeypatch):
    reg, err = _registry_from(monkeypatch, "{not json")
    assert reg == {} and "not valid JSON" in err
    reg, err = _registry_from(monkeypatch, "[1, 2]")
    assert reg == {} and "not a JSON object" in err
    reg, err = _registry_from(monkeypatch, '{"inject_profiles": {}}')
    assert reg == {} and "no 'tools' object" in err
    reg, err = _registry_from(monkeypatch, '{"tools": {}, "inject_profiles": 3}')
    assert reg == {} and "'inject_profiles' is not an object" in err


def test_a_registry_entry_overrides_the_fallback_and_junk_is_ignored(monkeypatch):
    reg, err = _registry_from(monkeypatch,
        '{"tools": {"meld": {"resume": "preloads"}, "junk": 5, "blank": {"resume": ""}}}')
    assert err is None
    table = merge._resume_table(reg)
    assert table["meld"] == "preloads"          # registry wins over the fallback
    assert "junk" not in table                  # non-object entry dropped
    assert "blank" not in table                 # empty capability ignored
    assert table["vimdiff"] == merge.RESUME_PRELOADS   # fallback still present


def test_the_command_table_is_untouched_by_the_capability_table():
    """i1a is a transparent shape change: every consumer of BUILTIN_TOOLS
    still sees name -> command string."""
    for name, cmd in merge.BUILTIN_TOOLS.items():
        assert isinstance(cmd, str) and "$MERGED" in cmd, name
