"""Repro: `ccs git <verb>` crashes with an unhandled traceback when
--checkout-dir points at a path that does not exist at all (as opposed to
a path that exists but isn't a git repo, which IS handled cleanly).

Found during the v0.5.1 human-checklist run (tester-unbounded, edge-case
extension "ccs git with pathological args"):

    ccs --checkout-dir <does-not-exist> git log
        -> Traceback ... NotADirectoryError: [WinError 267] The directory
           name is invalid

Root cause: `_run_git_verb()` in dazzle_claude_config/cli.py calls
`CheckoutRepo(checkout)` (dazzle_claude_config/gitops.py) directly. Every
OTHER verb (status/diff/collect/apply/merge) goes through `_setup()` in
cli.py, which does an explicit `if not checkout.is_dir(): raise
ManifestError("checkout not found: ...")` BEFORE touching git at all.
`_run_git_verb` has no equivalent pre-check, so `CheckoutRepo.__init__`
calls `subprocess.run(["git", "rev-parse", "--show-toplevel"],
cwd=self.path)` with a `cwd` that does not exist. On Windows this raises
`NotADirectoryError` (WinError 267) INSIDE subprocess.Popen's
_execute_child, before git even runs -- an OSError subclass, not GitError
or GitopsSafetyError, so `_run_git_verb`'s
`except (GitError, GitopsSafetyError)` does not catch it and the
traceback reaches the user. Every checklist item that expects "a clear
refusal on stderr ... never a traceback" (Section 1.3) is violated for
this specific input.

This is a DIAGNOSIS-ONLY repro (tester-unbounded: diagnose and report,
not fix). Not named test_*.py so it is not swept into the default
`pytest -q` collection (testpaths=["tests"], python_files="test_*.py")
and does not change the regression baseline count; run explicitly:

    pytest tests/one-offs/repro_ccs_git_nonexistent_checkout_dir.py -v

Two reasonable fixes for the maintainer to choose between (not applied
here):
  (a) `_run_git_verb` gains the same `if not checkout.is_dir(): ...
      "checkout not found: ..."` guard `_setup()` already has, OR
  (b) `CheckoutRepo.__init__` itself checks `self.path.is_dir()` first
      and raises `GitError` (which callers already catch) instead of
      letting the bare subprocess call surface an OS-level exception --
      this also protects any future caller of CheckoutRepo, not just
      the git verb.
"""
from __future__ import annotations

from dazzle_claude_config.cli import main


def test_ccs_git_on_nonexistent_checkout_dir_does_not_traceback(tmp_path, capsys):
    missing = tmp_path / "does-not-exist-at-all"
    assert not missing.exists()

    # Today (v0.5.1 working tree) this raises NotADirectoryError instead of
    # returning cleanly -- xfail(strict=True) documents the current broken
    # behavior and will flip to a clear failure (telling you to update the
    # test) the moment someone fixes it.
    try:
        rc = main(["--checkout-dir", str(missing), "--no-color", "git", "log"])
    except Exception as e:  # the actual, current, unwanted behavior
        import pytest
        pytest.fail(
            f"ccs git raised an unhandled {type(e).__name__} for a "
            f"nonexistent --checkout-dir instead of a clean refusal: {e!r}\n"
            "Compare with `ccs status --checkout-dir <same path>`, which "
            "handles this via _setup()'s checkout.is_dir() check and exits "
            "cleanly with 'checkout not found: ...' (exit 2)."
        )

    # If this ever runs to here, the bug is fixed: assert the good behavior.
    err = capsys.readouterr().err
    assert rc == 2
    assert "checkout not found" in err or "not a git repository" in err


def test_ccs_status_on_nonexistent_checkout_dir_refuses_cleanly(tmp_path, capsys):
    """Control: the SAME input against `status` already behaves correctly
    (via _setup()'s pre-check) -- proves the bug is specific to the `git`
    verb's checkout resolution path, not a general nonexistent-dir issue."""
    missing = tmp_path / "does-not-exist-at-all"
    rc = main(["--checkout-dir", str(missing), "--no-color", "status"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "checkout not found" in err
