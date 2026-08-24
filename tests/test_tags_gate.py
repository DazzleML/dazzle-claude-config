"""The tags gate: a manifest entry with `tags` applies -- and collects -- only
on a box that declares every one of them (~/claude/ccs-box.json). Plus the
`os` value validation that rides with it.

Fixture shape: the shared `env` manifest gains two tag-gated entries, one
per territory, with content on the checkout side (for apply) and on the live
side (for collect), so both directions are observable.
"""
from __future__ import annotations

import json

import pytest

from dazzle_claude_config import boxconfig
from dazzle_claude_config.apply import apply
from dazzle_claude_config.cli import main
from dazzle_claude_config.collect import collect
from dazzle_claude_config.manifest import Entry, Manifest, ManifestError
from dazzle_claude_config.syncmap import diff_all, entry_applies, entry_gate_reason

from conftest import MANIFEST, git


def _argv(env, verb, *extra):
    claude, user, checkout, _, _ = env
    return ["--no-color", "--checkout-dir", str(checkout), "--claude-dir",
            str(claude), "--user-claude", str(user), verb, *extra]


@pytest.fixture
def tagged(env):
    """env + a `machines/prod-vps/` entry (tags: prod-vps) delivering a
    file into ~/claude, and a `dotclaude/addenda` entry (tags: production)."""
    claude, user, checkout, _, roots = env
    data = json.loads(json.dumps(MANIFEST))
    data["entries"] += [
        {"repo": "machines/prod-vps/machine.md", "territory": "userclaude",
         "target": "claude-config/machine.md", "strategy": "copy",
         "tags": ["prod-vps"]},
        {"repo": "dotclaude/addenda/prod.md", "territory": "dotclaude",
         "target": "addenda/prod.md", "strategy": "copy",
         "tags": ["production"]},
    ]
    (checkout / "ccs-manifest.json").write_text(json.dumps(data, indent=1),
                                                encoding="utf-8")
    (checkout / "machines" / "prod-vps").mkdir(parents=True)
    (checkout / "machines" / "prod-vps" / "machine.md").write_text(
        "# the box's own manual\n", encoding="utf-8")
    (checkout / "dotclaude" / "addenda").mkdir(parents=True)
    (checkout / "dotclaude" / "addenda" / "prod.md").write_text(
        "# production addendum\n", encoding="utf-8")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "tagged entries")
    manifest = Manifest.load(checkout)
    return claude, user, checkout, manifest, roots


def _box(user, **body):
    (user / "ccs-box.json").write_text(json.dumps(body), encoding="utf-8")


# --- manifest -----------------------------------------------------------

def test_tags_parse_into_entry(tagged):
    *_, manifest, _ = tagged
    by_repo = {e.repo: e for e in manifest.entries}
    assert by_repo["machines/prod-vps/machine.md"].tags == ["prod-vps"]
    assert by_repo["dotclaude/CLAUDE.md"].tags == []


def test_bad_os_value_rejected(env):
    """A typo in `os` used to parse fine and never apply anywhere."""
    _, _, checkout, _, _ = env
    data = json.loads(json.dumps(MANIFEST))
    data["entries"][0]["os"] = "linux"
    (checkout / "ccs-manifest.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ManifestError, match="invalid os 'linux'"):
        Manifest.load(checkout)


def test_os_value_is_case_sensitive(env):
    """'Windows' would be stored as written and never equal os_key() --
    validation must refuse exactly what the gate would not match."""
    _, _, checkout, _, _ = env
    data = json.loads(json.dumps(MANIFEST))
    data["entries"][0]["os"] = "Windows"
    (checkout / "ccs-manifest.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ManifestError, match="invalid os 'Windows'"):
        Manifest.load(checkout)


@pytest.mark.parametrize("bad", ["prod-vps", ["Zeromeld"], [""], [3], ["a b"]])
def test_bad_tags_rejected(env, bad):
    _, _, checkout, _, _ = env
    data = json.loads(json.dumps(MANIFEST))
    data["entries"][0]["tags"] = bad
    (checkout / "ccs-manifest.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ManifestError, match="tag"):
        Manifest.load(checkout)


# --- the predicate ------------------------------------------------------

def test_entry_applies_requires_every_tag():
    e = Entry(repo="x", strategy="copy", tags=["a", "b"])
    assert not entry_applies(e)                       # no tags: safe default
    assert not entry_applies(e, {"a"})
    assert entry_applies(e, {"a", "b", "c"})
    assert entry_gate_reason(e, {"a"}) == "tags: b"
    assert entry_gate_reason(e, {"a", "b"}) is None


def test_untagged_entry_applies_everywhere():
    e = Entry(repo="x", strategy="copy")
    assert entry_applies(e) and entry_applies(e, {"anything"})


# --- box config ---------------------------------------------------------

def test_box_missing_means_no_tags(tmp_path):
    box = boxconfig.load(tmp_path)
    assert box.tags == frozenset() and box.name is None and box.errors == []


def test_box_loads_name_and_tags(tmp_path):
    _box(tmp_path, name="prod-vps", tags=["prod-vps", "production"])
    box = boxconfig.load(tmp_path)
    assert box.name == "prod-vps"
    assert box.tags == {"prod-vps", "production"}
    assert box.has_all(["production"]) and not box.has_all(["devbox"])


def test_box_broken_file_narrows_never_widens(tmp_path):
    """A malformed declaration reports and yields NO tags -- so a tag-gated
    entry stays off rather than suddenly landing on every box."""
    (tmp_path / "ccs-box.json").write_text("{not json", encoding="utf-8")
    box = boxconfig.load(tmp_path)
    assert box.tags == frozenset()
    assert box.errors and "JSONDecodeError" in box.errors[0]


def test_box_bad_tag_reported_others_kept(tmp_path):
    _box(tmp_path, name="x", tags=["ok", "Not OK"])
    box = boxconfig.load(tmp_path)
    assert box.tags == {"ok"}
    assert any("Not OK" in e for e in box.errors)


def test_box_env_override_replaces_file_tags(tmp_path, monkeypatch):
    _box(tmp_path, name="x", tags=["from-file"])
    monkeypatch.setenv(boxconfig.ENV_TAGS, "from-env, other")
    assert boxconfig.load(tmp_path).tags == {"from-env", "other"}


def test_box_env_empty_means_no_tags_for_this_run(tmp_path, monkeypatch):
    """`CCS_BOX_TAGS=` (set, empty) is the one-off way to run as a box with
    no tags -- it must replace the file's tags, not be ignored as falsy."""
    _box(tmp_path, name="x", tags=["from-file"])
    monkeypatch.setenv(boxconfig.ENV_TAGS, "")
    assert boxconfig.load(tmp_path).tags == frozenset()


def test_box_bad_name_reported_not_set(tmp_path):
    _box(tmp_path, name="Zero Meld", tags=["ok"])
    box = boxconfig.load(tmp_path)
    assert box.name is None
    assert any("bad name" in e for e in box.errors)


def test_write_template_never_overwrites_and_writes_valid_json(tmp_path):
    p = boxconfig.write_template(tmp_path)              # no name
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data == {"name": None, "tags": []}
    assert boxconfig.load(tmp_path).errors == []         # what it wrote, loads clean
    p.write_text('{"name": "keep", "tags": ["keep"]}', encoding="utf-8")
    boxconfig.write_template(tmp_path, name="clobber")
    assert boxconfig.load(tmp_path).name == "keep"


# --- both directions ----------------------------------------------------

def test_apply_delivers_tagged_entry_only_with_tag(tagged, backup_dir):
    claude, user, checkout, manifest, roots = tagged
    r = apply(manifest, checkout, roots, backup_dir)
    assert not (user / "claude-config" / "machine.md").exists()
    assert not (claude / "addenda" / "prod.md").exists()
    assert "machines/prod-vps/machine.md" not in " ".join(r.copied)

    r = apply(manifest, checkout, roots, backup_dir, box_tags={"prod-vps"})
    assert (user / "claude-config" / "machine.md").read_text(encoding="utf-8") \
        == "# the box's own manual\n"
    assert not (claude / "addenda" / "prod.md").exists()   # other tag still off


def test_collect_never_spreads_a_tagged_file_from_an_untagged_box(tagged):
    """The important half: a box-only file present live must not be staged
    into the checkout by a box that does not carry the tag."""
    _, user, checkout, manifest, roots = tagged
    (user / "claude-config").mkdir()
    (user / "claude-config" / "machine.md").write_text("# edited on this box\n",
                                                       encoding="utf-8")
    collect(manifest, checkout, roots)
    assert (checkout / "machines" / "prod-vps" / "machine.md").read_text(
        encoding="utf-8") == "# the box's own manual\n"

    collect(manifest, checkout, roots, box_tags={"prod-vps"})
    assert (checkout / "machines" / "prod-vps" / "machine.md").read_text(
        encoding="utf-8") == "# edited on this box\n"


def test_status_ignores_tagged_entries_without_the_tag(tagged):
    *_, checkout, manifest, roots = tagged
    assert len(diff_all(manifest, checkout, roots)) == 3
    assert len(diff_all(manifest, checkout, roots, {"prod-vps"})) == 4


# --- through the CLI ----------------------------------------------------

def test_cli_reads_box_file_and_applies(tagged, capsys):
    claude, user, *_ = tagged
    _box(user, name="prod-vps", tags=["prod-vps"])
    assert main(_argv(tagged, "apply")) == 0
    assert (user / "claude-config" / "machine.md").exists()
    assert not (claude / "addenda" / "prod.md").exists()


def test_cli_status_long_lists_gated_entries_with_reason(tagged, capsys):
    _, user, *_ = tagged
    _box(user, name="devbox", tags=["devbox"])
    main(_argv(tagged, "status", "--long", "--no-fetch"))
    out = capsys.readouterr().out
    assert "not for   box devbox (tags declared: devbox)" in out
    assert "machines/prod-vps/machine.md -- needs tags: prod-vps" in out
    assert "dotclaude/addenda/prod.md -- needs tags: production" in out


def test_cli_status_default_form_stays_quiet(tagged, capsys):
    main(_argv(tagged, "status", "--no-fetch"))
    assert "not for" not in capsys.readouterr().out


def test_cli_warns_on_broken_box_file(tagged, capsys):
    _, user, *_ = tagged
    (user / "ccs-box.json").write_text("[1, 2]", encoding="utf-8")
    main(_argv(tagged, "status", "--no-fetch"))
    assert "warning: box config:" in capsys.readouterr().out


# --- a miss that is really a gate (tester run-01, findings 2 and 3) ------

def test_only_prefix_that_reaches_only_a_gated_entry_says_so(tagged, capsys):
    """`--only machines/prod-vps` on an untagged box used to print the same
    'matched no manifest entries' as a typo; the fix is a tag, and the
    warning now says which entry and which tag."""
    main(_argv(tagged, "apply", "--only", "machines/prod-vps", "--dry-run"))
    out = capsys.readouterr().out
    assert "matches only entries this box is not tagged for" in out
    assert "machines/prod-vps/machine.md (needs tags: prod-vps)" in out
    assert "matched no manifest entries" not in out


def test_only_real_typo_still_says_no_match(tagged, capsys):
    main(_argv(tagged, "apply", "--only", "machines/nope", "--dry-run"))
    assert "matched no manifest entries" in capsys.readouterr().out


def test_diff_on_a_gated_off_path_names_the_gate(tagged, capsys):
    rc = main(_argv(tagged, "diff", "machines/prod-vps/machine.md"))
    err = capsys.readouterr().err
    assert rc == 2
    assert "not for this box: 'machines/prod-vps/machine.md'" in err
    assert "needs tags: prod-vps" in err
    assert "no such file in any manifest entry" not in err


# --- apply --reseed (the migration move for an existing seeded file) -----

def test_reseed_backs_up_and_overwrites_one_seed(tagged, backup_dir):
    """The migration move: a box whose live file predates the seed gets the
    payload's fresh seed, old copy in this run's backup dir first. Uses the
    fixture's real seed entry (settings.local.seed.json -> settings.local.json;
    CLAUDE.md is still a copy entry in this fixture)."""
    claude, user, checkout, manifest, roots = tagged
    from dazzle_claude_config.apply import apply
    (claude / "settings.local.json").write_text('{"mine": true}', encoding="utf-8")
    r = apply(manifest, checkout, roots, backup_dir)
    assert (claude / "settings.local.json").read_text(encoding="utf-8") == '{"mine": true}'
    r = apply(manifest, checkout, roots, backup_dir, reseed="settings.local.json")
    assert r.reseeded == ["settings.local.json"]
    assert (claude / "settings.local.json").read_text(encoding="utf-8") == "{}\n"
    backed = list(r.backup_dir.rglob("settings.local.json"))
    assert backed and backed[0].read_text(encoding="utf-8") == '{"mine": true}'


def test_reseed_dry_run_changes_nothing(tagged, backup_dir):
    claude, user, checkout, manifest, roots = tagged
    from dazzle_claude_config.apply import apply
    (claude / "settings.local.json").write_text('{"precious": 1}', encoding="utf-8")
    r = apply(manifest, checkout, roots, backup_dir, reseed="settings.local.json", dry_run=True)
    assert r.reseeded == ["settings.local.json"]
    assert (claude / "settings.local.json").read_text(encoding="utf-8") == '{"precious": 1}'


def test_reseed_without_a_live_file_warns_not_seeds(tagged, backup_dir, capsys):
    claude, user, checkout, manifest, roots = tagged
    rc = main(_argv(tagged, "apply", "--reseed", "nope.md"))
    out = capsys.readouterr().out
    assert "matched no seed entry with an existing live file" in out
    assert rc == 0


def test_reseed_never_touches_copy_entries(tagged, backup_dir):
    claude, user, checkout, manifest, roots = tagged
    from dazzle_claude_config.apply import apply
    (claude / "agents" / "oracle.md").write_text("mine\n", encoding="utf-8")
    r = apply(manifest, checkout, roots, backup_dir, reseed="agents/oracle.md")
    assert r.reseeded == []


def test_reseed_on_an_absent_file_just_seeds_without_the_warning(tagged, capsys):
    """--reseed where nothing exists yet is an ordinary seed; the no-match
    warning must not claim 'nothing done' over a seed that happened."""
    claude, user, *_ = tagged
    assert not (claude / "settings.local.json").exists()
    rc = main(_argv(tagged, "apply", "--reseed", "settings.local.json"))
    out = capsys.readouterr().out
    assert "seeded settings.local.json" in out
    assert "matched no seed entry" not in out
    assert rc == 0
