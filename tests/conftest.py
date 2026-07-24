"""Shared fixtures: a fake live environment + a git payload checkout."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from dazzle_claude_config.manifest import Manifest

GIT_ID = ["-c", "user.email=test@test.invalid", "-c", "user.name=ccs-test",
          "-c", "commit.gpgsign=false"]


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *GIT_ID, *args], cwd=str(cwd),
                            capture_output=True, text=True)
    assert result.returncode == 0, f"git {args}: {result.stderr}"
    return result.stdout


MANIFEST = {
    "manifest_version": 1,
    "territories": {
        "dotclaude": {"root_var": "CLAUDE_DIR", "repo_dir": "dotclaude"},
        "userclaude": {"root_var": "USER_CLAUDE", "repo_dir": "userclaude"},
    },
    "entries": [
        {"repo": "dotclaude/CLAUDE.md", "territory": "dotclaude",
         "target": "CLAUDE.md", "strategy": "copy"},
        {"repo": "dotclaude/agents", "territory": "dotclaude",
         "target": "agents", "strategy": "copy"},
        {"repo": "userclaude/scripts", "territory": "userclaude",
         "target": "scripts", "strategy": "copy"},
        {"repo": "settings/settings.local.seed.json", "territory": "dotclaude",
         "target": "settings.local.json", "strategy": "seed-if-absent"},
        {"repo": "settings/settings.base.json", "territory": "dotclaude",
         "target": "settings.json", "strategy": "render"},
    ],
    "collect_exclude": ["**/__pycache__/**", "*.pyc"],
    "deny": ["*.secret"],
}


@pytest.fixture
def env(tmp_path: Path):
    """(claude_dir, user_dir, checkout, manifest, roots) with matching content."""
    claude = tmp_path / "dot_claude"
    user = tmp_path / "user_claude"
    checkout = tmp_path / "checkout"
    (claude / "agents").mkdir(parents=True)
    (user / "scripts").mkdir(parents=True)

    (claude / "CLAUDE.md").write_text("# global memory v1\n", encoding="utf-8")
    (claude / "agents" / "oracle.md").write_text("oracle agent\n", encoding="utf-8")
    (user / "scripts" / "guard.js").write_text("// guard v1\n", encoding="utf-8")

    (checkout / "dotclaude" / "agents").mkdir(parents=True)
    (checkout / "userclaude" / "scripts").mkdir(parents=True)
    (checkout / "settings").mkdir(parents=True)
    (checkout / "dotclaude" / "CLAUDE.md").write_text("# global memory v1\n",
                                                      encoding="utf-8")
    (checkout / "dotclaude" / "agents" / "oracle.md").write_text("oracle agent\n",
                                                                 encoding="utf-8")
    (checkout / "userclaude" / "scripts" / "guard.js").write_text("// guard v1\n",
                                                                  encoding="utf-8")
    (checkout / "settings" / "settings.local.seed.json").write_text("{}\n",
                                                                    encoding="utf-8")
    (checkout / "settings" / "settings.base.json").write_text("{}\n",
                                                              encoding="utf-8")
    (checkout / "ccs-manifest.json").write_text(json.dumps(MANIFEST, indent=1),
                                                encoding="utf-8")
    git(checkout, "init", "-q", "-b", "main")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "seed")

    manifest = Manifest.load(checkout)
    roots = {"CLAUDE_DIR": claude, "USER_CLAUDE": user}
    return claude, user, checkout, manifest, roots


@pytest.fixture
def backup_dir(tmp_path: Path) -> Path:
    return tmp_path / "backups"
