"""Environment resolution: territories, OS key, hostname.

Named platform_info (not platform) to avoid confusion with the stdlib module.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path


def os_key() -> str:
    """Manifest OS discriminator: 'windows' or 'posix'."""
    return "windows" if os.name == "nt" else "posix"


def hostname() -> str:
    return os.environ.get("COMPUTERNAME") or socket.gethostname().split(".")[0]


def claude_dir(override: str | None = None) -> Path:
    """~/.claude, honoring CLAUDE_CONFIG_DIR (csb parity) and CLI override."""
    if override:
        return Path(override).expanduser().resolve()
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".claude").resolve()


def user_claude_dir(override: str | None = None) -> Path:
    """~/claude -- the sibling user-territory directory."""
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / "claude").resolve()


def default_checkout_dir() -> Path:
    """Default payload checkout location: inside user territory, outside ~/.claude."""
    return user_claude_dir() / "dazzle-claude-code-config"


def backup_root() -> Path:
    return user_claude_dir() / "backups" / "ccs"


def territory_roots(claude_override: str | None = None,
                    user_claude_override: str | None = None) -> dict[str, Path]:
    """Map manifest root_var names to live directories."""
    return {
        "CLAUDE_DIR": claude_dir(claude_override),
        "USER_CLAUDE": user_claude_dir(user_claude_override),
    }
