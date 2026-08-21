"""Git operations constrained to the payload CHECKOUT.

Safety invariant (acceptance check A4, born from the 2026-04-05 home-repo
incident): this module refuses to operate on a repository whose toplevel is
the user's home directory, and it exposes NO branch-switching operations.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


class GitopsSafetyError(RuntimeError):
    pass


class GitError(RuntimeError):
    pass


def _run(args: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    result = subprocess.run(
        ["git"] + args, cwd=str(cwd) if cwd else None,
        capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


class CheckoutRepo:
    """A git repo handle that is structurally unable to touch the home repo."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        home = Path.home().resolve()
        if self.path == home:
            raise GitopsSafetyError(
                f"refusing to operate on the home directory repo: {self.path}")
        rc, out, err = _run(["rev-parse", "--show-toplevel"], cwd=self.path)
        if rc != 0:
            raise GitError(f"not a git repository: {self.path}: {err.strip()}")
        toplevel = Path(out.strip()).resolve()
        if toplevel == home:
            raise GitopsSafetyError(
                f"path {self.path} belongs to the HOME repo ({toplevel}); "
                "ccs never operates on the home repository")
        if toplevel != self.path:
            # A plain dir nested inside SOME repo (e.g. under %TEMP% inside a
            # home repo) must not silently bind to that parent repository.
            #
            # SAFETY refusal, not a "no repo here" report -- hence
            # GitopsSafetyError, which the CLI deliberately does not catch.
            # As GitError it was downgraded to "plain directory checkout,
            # A8/A11 skipped", silently disabling the git-index and
            # merge-conflict guards for exactly the ambiguous case this
            # check exists to catch (found by the v0.2.1 release checklist run).
            raise GitopsSafetyError(
                f"not a git repository root: {self.path} "
                f"(inside repo {toplevel}) -- ccs will not bind to a parent "
                "repository; move the checkout outside it, or `git init` it")
        self.toplevel = toplevel

    @classmethod
    def clone(cls, url: str, dest: Path) -> "CheckoutRepo":
        dest = Path(dest).resolve()
        if dest == Path.home().resolve():
            raise GitopsSafetyError("refusing to clone onto the home directory")
        rc, _, err = _run(["clone", url, str(dest)])
        if rc != 0:
            raise GitError(f"clone failed: {err.strip()}")
        return cls(dest)

    def porcelain(self) -> list[str]:
        rc, out, err = _run(["status", "--porcelain"], cwd=self.path)
        if rc != 0:
            raise GitError(f"status failed: {err.strip()}")
        return [l for l in out.splitlines() if l.strip()]

    def has_conflicts(self) -> bool:
        """A11: unresolved merge conflicts in the arena."""
        conflict_codes = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
        return any(l[:2] in conflict_codes for l in self.porcelain())

    def branch_info(self) -> str:
        rc, out, _ = _run(["status", "-sb"], cwd=self.path)
        return out.splitlines()[0] if rc == 0 and out else "?"

    def upstream(self) -> str | None:
        """`origin/main`-style name of the tracking branch, or None."""
        rc, out, _ = _run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                          cwd=self.path)
        return out.strip() if rc == 0 and out.strip() else None

    def fetch(self, timeout: int = 15) -> tuple[bool | None, str]:
        """Refresh the remote-tracking ref the branch line is measured against.

        Returns (ok, detail): (True, "") on success; (False, reason) on any
        failure -- timeout, offline, auth -- with git's first stderr line kept
        so the user can tell those apart; (None, "no upstream") when there is
        nothing to fetch against.

        Why this exists: `status` read `git status -sb`, which compares against
        whatever the LAST fetch left behind, and printed "in sync with
        origin/main" -- a claim about the remote that nothing had checked
        (2026-08-21, the first two-machine round trip; the line was true only
        because the operator had fetched by hand). A fetch touches remote-
        tracking refs only: no local branch, no index, no working tree, so
        `status` stays read-only in every sense that matters.

        Non-interactive by construction: GIT_TERMINAL_PROMPT=0 and
        GCM_INTERACTIVE=never turn a would-be credential prompt into a fast
        failure instead of a hang inside a read-only verb; the timeout is the
        backstop for a remote that accepts the connection and stalls.
        """
        up = self.upstream()
        if not up:
            return None, "no upstream"
        remote = up.split("/", 1)[0]
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
        try:
            r = subprocess.run(["git", "fetch", remote, "--quiet", "--prune"],
                               cwd=str(self.path), capture_output=True, text=True,
                               env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f"timed out after {timeout}s"
        if r.returncode != 0:
            first = next((l for l in (r.stderr or r.stdout).splitlines() if l.strip()), "")
            return False, first.strip() or f"git fetch exited {r.returncode}"
        return True, ""

    def ahead_behind(self) -> tuple[int | None, int | None]:
        """(ahead, behind) relative to the upstream; (None, None) without one."""
        up = self.upstream()
        if not up:
            return None, None
        rc, out, _ = _run(["rev-list", "--left-right", "--count", f"HEAD...{up}"],
                          cwd=self.path)
        if rc != 0 or not out.strip():
            return None, None
        a, b = out.split()
        return int(a), int(b)

    def pull(self) -> str:
        rc, out, err = _run(["pull", "--no-rebase"], cwd=self.path)
        if rc != 0:
            raise GitError(f"pull failed: {err.strip() or out.strip()}")
        return out.strip()

    def push(self) -> str:
        rc, out, err = _run(["push"], cwd=self.path)
        if rc != 0:
            raise GitError(f"push failed: {err.strip() or out.strip()}")
        return (err or out).strip()  # git push reports to stderr

    def check_ignored(self, rel_paths: list[str]) -> list[str]:
        """A8: which of these repo-relative paths does git ignore/exclude?

        Catches machine-level info/exclude injections that would silently
        drop copied files from the index (the Phase 0 CLAUDE.md incident).
        """
        if not rel_paths:
            return []
        proc = subprocess.run(
            ["git", "check-ignore", "--stdin"], cwd=str(self.path),
            input="\n".join(rel_paths), capture_output=True, text=True)
        return [l for l in proc.stdout.splitlines() if l.strip()]

    def path_in_history(self, rel_path: str) -> bool:
        """Has this path ever existed in the repo, on any branch?

        Distinguishes "the checkout deleted it" from "the checkout never had
        it". Without that, a brand-new local file looks identical to one the
        other machine removed, and `apply` reports it as a pending removal --
        implying an order of operations the user does not actually need.
        """
        rc, out, _ = _run(["log", "--all", "--oneline", "--", rel_path],
                          cwd=self.path)
        return rc == 0 and bool(out.strip())
