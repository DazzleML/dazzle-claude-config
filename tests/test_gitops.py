"""gitops: A4 home-repo isolation."""
from pathlib import Path

import pytest

from dazzle_claude_config.gitops import CheckoutRepo, GitError, GitopsSafetyError

from conftest import git


def test_a4_home_path_refused(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr("dazzle_claude_config.gitops.Path.home",
                        staticmethod(lambda: fake_home))
    with pytest.raises(GitopsSafetyError, match="home directory"):
        CheckoutRepo(fake_home)


def test_a4_repo_rooted_at_home_refused(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    git(fake_home, "init", "-q", "-b", "main")
    monkeypatch.setattr("dazzle_claude_config.gitops.Path.home",
                        staticmethod(lambda: fake_home))
    # A path INSIDE the home repo resolves to the home toplevel -> refused
    with pytest.raises(GitopsSafetyError, match="HOME repo"):
        CheckoutRepo(fake_home / ".claude")


def test_non_repo_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("dazzle_claude_config.gitops.Path.home",
                        staticmethod(lambda: tmp_path / "elsewhere"))
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(GitError, match="not a git repository"):
        CheckoutRepo(plain)


def test_normal_checkout_accepted(env, monkeypatch):
    _, _, checkout, _, _ = env
    monkeypatch.setattr("dazzle_claude_config.gitops.Path.home",
                        staticmethod(lambda: checkout.parent / "nonexistent-home"))
    repo = CheckoutRepo(checkout)
    assert repo.toplevel == checkout.resolve()
    assert not repo.has_conflicts()
    assert repo.branch_info().startswith("## main")
