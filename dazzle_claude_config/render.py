"""Terminal rendering: color, and the vocabulary ccs speaks to humans.

Color convention follows claude-session-backup's search_render.py so the
DazzleML Claude tools look like one family: bold cyan for identifiers and
paths, green for "nothing for you to do", yellow for "needs attention",
red for refusals and errors, dim for secondary detail.

Escapes are emitted ONLY to a TTY, and never when NO_COLOR is set (the
informal cross-tool standard) or --no-color is passed -- so piping ccs
into grep, a file, or CI logs yields clean text.
"""
from __future__ import annotations

import os
import re
import sys

_ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "cyan": "\033[36m",
    "yellow": "\033[33m",
    "green": "\033[32m",
    "red": "\033[31m",
    "magenta": "\033[35m",
    "bold_cyan": "\033[1;36m",
    "bold_green": "\033[1;32m",
    "bold_yellow": "\033[1;33m",
    "bold_red": "\033[1;31m",
}

_enabled = False


def supported(no_color: bool = False) -> bool:
    if no_color or os.environ.get("NO_COLOR"):
        return False
    if not getattr(sys.stdout, "isatty", lambda: False)():
        return False
    # Windows Terminal / VS Code / modern PowerShell handle ANSI, and
    # Python 3.6+ on Win10+ enables VT processing for console stdout
    # (CPython issue #30075). Trust it rather than probing.
    return True


def init(no_color: bool = False) -> None:
    """Decide once per run whether output carries color."""
    global _enabled
    _enabled = supported(no_color)


def c(name: str, text: str) -> str:
    if not _enabled:
        return text
    return f"{_ANSI[name]}{text}{_ANSI['reset']}"


def n_files(n: int) -> str:
    return f"{n} file" if n == 1 else f"{n} files"


def n_entries(n: int) -> str:
    return f"{n} entry" if n == 1 else f"{n} entries"


_BRANCH_RE = re.compile(
    r"^## (?P<local>[^.\s]+)(?:\.\.\.(?P<remote>\S+))?(?: \[(?P<track>[^\]]+)\])?")


def humanize_branch(raw: str) -> str:
    """Turn `git status -sb`'s first line into something readable.

    '## main...origin/main'            -> 'on main, in sync with origin/main'
    '## main...origin/main [ahead 2]'  -> 'on main, 2 ahead of origin/main'
    '## main' (no upstream)            -> 'on main, no upstream configured'
    """
    m = _BRANCH_RE.match(raw or "")
    if not m:
        if raw.startswith("## HEAD"):
            return "detached HEAD (not on a branch)"
        return raw or "?"
    local, remote, track = m.group("local"), m.group("remote"), m.group("track")
    if not remote:
        return f"on {local}, no upstream configured"
    if not track:
        return f"on {local}, in sync with {remote}"
    ahead = re.search(r"ahead (\d+)", track)
    behind = re.search(r"behind (\d+)", track)
    bits = []
    if ahead:
        bits.append(f"{ahead.group(1)} ahead")
    if behind:
        bits.append(f"{behind.group(1)} behind")
    return f"on {local}, {' / '.join(bits)} vs {remote}" if bits \
        else f"on {local}, in sync with {remote}"
