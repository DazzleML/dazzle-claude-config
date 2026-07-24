"""Layout-agnostic fallback: a bare ~/.claude mirror works without a manifest."""
import pytest

from dazzle_claude_config.cli import main
from dazzle_claude_config.collect import collect
from dazzle_claude_config.manifest import Manifest, ManifestError

from conftest import git


@pytest.fixture
def bare_mirror(tmp_path):
    """A checkout that is just someone's ~/.claude pushed as-is."""
    claude = tmp_path / "live_claude"
    user = tmp_path / "live_user"
    (claude / "skills").mkdir(parents=True)
    user.mkdir()
    co = tmp_path / "mirror"
    (co / "skills" / "myskill").mkdir(parents=True)
    (co / "agents").mkdir()
    (co / "CLAUDE.md").write_text("# my memory\n", encoding="utf-8")
    (co / "skills" / "myskill" / "SKILL.md").write_text("skill\n", encoding="utf-8")
    (co / "agents" / "helper.md").write_text("agent\n", encoding="utf-8")
    git(co, "init", "-q", "-b", "main")
    git(co, "add", "-A")
    git(co, "commit", "-q", "-m", "mirror")
    return claude, user, co


def test_implicit_manifest_detects_surfaces(bare_mirror):
    _, _, co = bare_mirror
    m = Manifest.implicit(co)
    repos = {e.repo for e in m.entries}
    assert repos == {"skills", "agents", "CLAUDE.md"}
    assert all(e.strategy == "copy" for e in m.entries)


def test_non_config_repo_rejected(tmp_path):
    plain = tmp_path / "random"
    (plain / "src").mkdir(parents=True)
    with pytest.raises(ManifestError, match="does not look like a Claude config dir"):
        Manifest.implicit(plain)


def test_cli_apply_from_bare_mirror(bare_mirror, capsys):
    claude, user, co = bare_mirror
    rc = main(["--checkout-dir", str(co), "--claude-dir", str(claude),
               "--user-claude", str(user), "apply"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "implicit ~/.claude layout" in out
    assert (claude / "CLAUDE.md").read_text(encoding="utf-8") == "# my memory\n"
    assert (claude / "skills" / "myskill" / "SKILL.md").exists()
    assert (claude / "agents" / "helper.md").exists()


def test_implicit_collect_still_hard_denies(bare_mirror):
    claude, _, co = bare_mirror
    (claude / ".credentials.json").write_text("{}", encoding="utf-8")
    (claude / "agents").mkdir(exist_ok=True)
    (claude / "agents" / "new.md").write_text("n\n", encoding="utf-8")
    m = Manifest.implicit(co)
    roots = {"CLAUDE_DIR": claude, "USER_CLAUDE": co.parent / "live_user"}
    r = collect(m, co, roots)
    assert "agents/new.md" in r.copied
    # .credentials.json sits at the territory root, not inside a manifest
    # entry, so the allowlist never even visits it -- and HARD_DENY would
    # refuse it if a future entry covered it.
    assert not (co / ".credentials.json").exists()


def test_explicit_manifest_still_wins(env, capsys):
    claude, user, checkout, _, _ = env
    rc = main(["--checkout-dir", str(checkout), "--claude-dir", str(claude),
               "--user-claude", str(user), "status"])
    assert rc == 0
    assert "implicit" not in capsys.readouterr().out
