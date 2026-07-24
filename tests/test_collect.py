"""collect: guard-stack behavior including A1 (refusals) and A8 (index check)."""
from dazzle_claude_config.collect import collect
from dazzle_claude_config.gitops import CheckoutRepo


def test_clean_env_collects_nothing(env):
    _, _, checkout, manifest, roots = env
    r = collect(manifest, checkout, roots)
    assert r.copied == [] and r.refusals == 0


def test_new_and_modified_files_collected(env):
    claude, user, checkout, manifest, roots = env
    (claude / "agents" / "newbie.md").write_text("new agent\n", encoding="utf-8")
    (claude / "CLAUDE.md").write_text("# global memory v2\n", encoding="utf-8")
    r = collect(manifest, checkout, roots)
    assert "dotclaude/agents/newbie.md" in r.copied
    assert "dotclaude/CLAUDE.md" in r.copied
    assert (checkout / "dotclaude" / "agents" / "newbie.md").read_text(
        encoding="utf-8") == "new agent\n"
    assert (checkout / "dotclaude" / "CLAUDE.md").read_text(
        encoding="utf-8") == "# global memory v2\n"


def test_dry_run_copies_nothing(env):
    claude, _, checkout, manifest, roots = env
    (claude / "agents" / "newbie.md").write_text("new agent\n", encoding="utf-8")
    r = collect(manifest, checkout, roots, dry_run=True)
    assert "dotclaude/agents/newbie.md" in r.copied
    assert not (checkout / "dotclaude" / "agents" / "newbie.md").exists()


def test_a1_planted_secret_refused(env):
    claude, _, checkout, manifest, roots = env
    planted = "key = sk-ant-api03-" + "z" * 24 + "\n"
    (claude / "agents" / "leaky.md").write_text(planted, encoding="utf-8")
    r = collect(manifest, checkout, roots)
    assert len(r.refused_secrets) == 1
    assert r.refused_secrets[0].rel_path == "dotclaude/agents/leaky.md"
    assert not (checkout / "dotclaude" / "agents" / "leaky.md").exists()


def test_a1_denied_filename_refused(env):
    claude, _, checkout, manifest, roots = env
    (claude / "agents" / ".credentials.json").write_text("{}", encoding="utf-8")
    (claude / "agents" / "notes.secret").write_text("shh", encoding="utf-8")
    r = collect(manifest, checkout, roots)
    refused = [rel for rel, _ in r.refused_denied]
    assert "dotclaude/agents/.credentials.json" in refused
    assert "dotclaude/agents/notes.secret" in refused  # manifest deny extends hard deny
    assert not (checkout / "dotclaude" / "agents" / ".credentials.json").exists()


def test_collect_exclude_honored(env):
    claude, _, checkout, manifest, roots = env
    pyc = claude / "agents" / "__pycache__"
    pyc.mkdir()
    (pyc / "junk.cpython-312.pyc").write_bytes(b"\x00")
    r = collect(manifest, checkout, roots)
    assert r.copied == []
    assert not (checkout / "dotclaude" / "agents" / "__pycache__").exists()


def test_missing_live_reported_not_deleted(env):
    claude, _, checkout, manifest, roots = env
    (claude / "agents" / "oracle.md").unlink()
    r = collect(manifest, checkout, roots)
    assert "dotclaude/agents/oracle.md" in r.missing_live
    assert (checkout / "dotclaude" / "agents" / "oracle.md").exists()


def test_a8_git_ignored_copy_detected(env, monkeypatch):
    """A newly collected file swallowed by a machine-level exclude is flagged.

    (Tracked files are immune to excludes -- git check-ignore consults the
    index first -- so A8 specifically protects NEW files, matching the
    Phase 0 incident where an unanchored 'CLAUDE.md' in .git/info/exclude
    silently dropped a first-time file.)
    """
    claude, _, checkout, manifest, roots = env
    monkeypatch.setattr("dazzle_claude_config.gitops.Path.home",
                        staticmethod(lambda: checkout.parent / "nonexistent-home"))
    (checkout / ".git" / "info").mkdir(exist_ok=True)
    (checkout / ".git" / "info" / "exclude").write_text("newbie.md\n",
                                                        encoding="utf-8")
    (claude / "agents" / "newbie.md").write_text("new agent\n", encoding="utf-8")
    repo = CheckoutRepo(checkout)
    r = collect(manifest, checkout, roots, repo=repo)
    assert "dotclaude/agents/newbie.md" in r.copied
    assert "dotclaude/agents/newbie.md" in r.git_ignored
