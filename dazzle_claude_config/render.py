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


def n_settings(n: int) -> str:
    """One place to say "3 settings" or "1 setting".

    Here rather than inline at each call site because this exact phrase has
    been got wrong twice in one release, both times the same way: a plural
    corrected where somebody was looking and left wrong on the next line
    down. A phrase built once cannot disagree with itself.
    """
    return f"{n} setting" if n == 1 else f"{n} settings"


_BRANCH_RE = re.compile(
    r"^## (?P<local>[^.\s]+)(?:\.\.\.(?P<remote>\S+))?(?: \[(?P<track>[^\]]+)\])?")


UNSPECIFIED = object()   # humanize_branch: caller said nothing about fetching


def humanize_branch(raw: str, fetched=UNSPECIFIED, detail: str = "") -> str:
    """Turn `git status -sb`'s first line into something readable.

    '## main...origin/main'            -> 'on main, in sync with origin/main'
    '## main...origin/main [ahead 2]'  -> 'on main, 2 ahead vs origin/main'
    '## main...origin/main [behind 3]' -> 'on main, 3 behind vs origin/main -- git pull'
    '## main' (no upstream)            -> 'on main, no upstream configured'

    `fetched` says whether a fetch ran THIS run: True -> the line is a claim
    about the remote; None -> it was skipped (`--no-fetch`), so "in sync"
    becomes "in sync as last fetched"; False -> it failed, and the negative is
    withheld: "pull status unknown -- fetch failed: <detail>". Left
    UNSPECIFIED, the line is the bare parse, for callers that only format. Saying "in
    sync" after a failed or skipped fetch is a confident claim nothing
    checked -- the branch-line twin of calling a file "both sides" without a
    base.
    """
    m = _BRANCH_RE.match(raw or "")
    if not m:
        if raw.startswith("## HEAD"):
            return "detached HEAD (not on a branch)"
        return raw or "?"
    local, remote, track = m.group("local"), m.group("remote"), m.group("track")
    if not remote:
        return f"on {local}, no upstream configured"
    ahead = re.search(r"ahead (\d+)", track or "")
    behind = re.search(r"behind (\d+)", track or "")
    gone = bool(track) and "gone" in track
    bits = []
    if ahead:
        bits.append(f"{ahead.group(1)} ahead")
    if behind:
        bits.append(f"{behind.group(1)} behind")
    if gone:
        return f"on {local}, upstream {remote} no longer exists on the remote"
    if fetched is False:
        why = f": {detail}" if detail else ""
        state = f"{' / '.join(bits)} vs {remote} as last fetched" if bits else \
            f"vs {remote} as last fetched"
        return f"on {local}, pull status unknown -- fetch failed{why} ({state})"
    line = f"on {local}, {' / '.join(bits)} vs {remote}" if bits \
        else f"on {local}, in sync with {remote}"
    if fetched is None:
        line += " as last fetched"
    if behind and fetched is not UNSPECIFIED:
        line += " -- git pull" if fetched else " -- git fetch to confirm"
    return line


def branch_name(raw: str) -> str | None:
    """Just the local branch name out of `git status -sb`'s first line."""
    m = _BRANCH_RE.match(raw or "")
    return m.group("local") if m else None


def remote_host(url: str | None) -> str | None:
    """Boil a git remote URL down to `host/owner/repo` for the status line.

    https://github.com/o/r.git  -> github.com/o/r
    git@github.com:o/r.git      -> github.com/o/r
    ssh://git@host:2222/o/r.git -> host:2222/o/r
    Anything unparseable comes back AS IS -- a raw URL on the remote line
    beats a clever parse that lies about where the remote lives (#22's
    brutal-fallback rule). None means no remote at all.
    """
    if not url:
        return None
    u = url.strip()
    m = re.match(r"^(?:https?|ssh|git)://(?:[^/@]+@)?(?P<rest>.+)$", u)
    if m:
        rest = m.group("rest").rstrip("/")
        return rest[:-4] if rest.endswith(".git") else rest
    m = re.match(r"^(?:[^@\s]+@)(?P<host>[^:\s]+):(?P<path>[^\s]+)$", u)
    if m:
        path = m.group("path").rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        return f"{m.group('host')}/{path}"
    return u


def humanize_remote(raw: str, fetched=UNSPECIFIED, detail: str = "",
                    pulled: tuple[int, bool, str] | None = None) -> str:
    """The remote leg's state: humanize_branch's facts, phrased for a line
    that already names the remote (so no `with origin/main` echo), plus the
    auto_pull outcome when one happened.

    pulled: None -- no fast-forward was attempted this run;
            (n, True, "")      -- was n behind, fast-forwarded just now;
            (n, False, reason) -- n behind but the fast-forward was refused
                                  (divergence, dirty file); reason verbatim.
    """
    if (raw or "").startswith("## HEAD"):
        # Before the regex: `## HEAD (no branch)` parses as a branch named
        # HEAD with no upstream, which is a lie with better grammar.
        return "detached HEAD (not on a branch)"
    m = _BRANCH_RE.match(raw or "")
    if not m:
        return raw or "?"
    local, remote, track = m.group("local"), m.group("remote"), m.group("track")
    if not remote:
        return f"{local}, no upstream configured"
    if bool(track) and "gone" in track:
        return f"{local}, upstream no longer exists on the remote"
    if pulled is not None:
        n, ok, reason = pulled
        if ok:
            return f"{local}, your checkout was {n} behind -- fast-forwarded"
        return (f"{local}, your checkout is {n} behind -- "
                f"fast-forward refused: {reason}")
    ahead = re.search(r"ahead (\d+)", track or "")
    behind = re.search(r"behind (\d+)", track or "")
    if fetched is False:
        why = f": {detail}" if detail else ""
        bits = [b for b in (ahead and f"your checkout {ahead.group(1)} ahead",
                            behind and f"your checkout {behind.group(1)} behind") if b]
        stale = f" ({' / '.join(bits)} as last fetched)" if bits \
            else " (as last fetched)"
        return f"{local}, pull status unknown -- fetch failed{why}{stale}"
    if ahead and behind:
        return (f"{local}, your checkout is {ahead.group(1)} ahead and "
                f"{behind.group(1)} behind -- diverged; "
                f"resolve in the checkout (ccs git ...)")
    if behind:
        hint = "ccs status --pull" if fetched else "ccs git fetch to confirm"
        stale = "" if fetched else " as last fetched"
        return f"{local}, your checkout is {behind.group(1)} behind{stale} -- {hint}"
    if ahead:
        return f"{local}, your checkout is {ahead.group(1)} ahead -- ccs git push to share"
    return f"{local}, in sync" + ("" if fetched is True else " as last fetched"
                                  if fetched is None else "")
