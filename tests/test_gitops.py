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


def test_a4_nested_plain_dir_in_other_repo_refused(tmp_path, monkeypatch):
    """Guard (c) from the design contract: a plain (non-git) directory
    nested inside SOME OTHER (non-home) git repo must be refused --
    toplevel != path -- so ccs never silently binds operations to the
    wrong repository root. This path (as opposed to the no-repo-at-all
    and home-repo cases already covered above) had no prior test."""
    fake_home = tmp_path / "nonexistent-home"
    monkeypatch.setattr("dazzle_claude_config.gitops.Path.home",
                        staticmethod(lambda: fake_home))
    outer = tmp_path / "outer_repo"
    outer.mkdir()
    git(outer, "init", "-q", "-b", "main")
    (outer / "README.md").write_text("outer\n", encoding="utf-8")
    git(outer, "add", "-A")
    git(outer, "commit", "-q", "-m", "seed outer")
    nested_plain = outer / "some" / "nested" / "plain_dir"
    nested_plain.mkdir(parents=True)
    (nested_plain / "file.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(GitError, match="not a git repository root"):
        CheckoutRepo(nested_plain)
