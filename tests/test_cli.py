"""CLI: A7 exit-code contract (0 clean / 1 drift / 2 error) via main(argv)."""
import json
import subprocess
import sys

from dazzle_claude_config.cli import main

from conftest import git


def _argv(env, verb, *extra):
    claude, user, checkout, _, _ = env
    return ["--checkout-dir", str(checkout), "--claude-dir", str(claude),
            "--user-claude", str(user), verb, *extra]


def test_a7_status_clean_exit_0(env, capsys):
    assert main(_argv(env, "status")) == 0
    assert "status: clean" in capsys.readouterr().out


def test_a7_status_drift_exit_1(env, capsys):
    claude, *_ = env
    (claude / "agents" / "newbie.md").write_text("new\n", encoding="utf-8")
    assert main(_argv(env, "status")) == 1
    assert "drift" in capsys.readouterr().out


def test_a7_missing_checkout_exit_2(env, capsys):
    claude, user, checkout, _, _ = env
    rc = main(["--checkout-dir", str(checkout / "nope"), "--claude-dir",
               str(claude), "--user-claude", str(user), "status"])
    assert rc == 2
    assert "checkout not found" in capsys.readouterr().err


def test_a7_denied_file_exit_0_with_note(env, capsys):
    """R6: deny-matched files = intended protection = exit 0 with a note.

    Vocabulary is deliberate: "protected ... stays local" reads as the
    guard working, where the older "denied, never syncs" read as an
    error nobody could act on (human-test finding 2026-07-24)."""
    claude, *_ = env
    (claude / "agents" / ".credentials.json").write_text("{}", encoding="utf-8")
    assert main(_argv(env, "collect")) == 0
    out = capsys.readouterr().out
    assert "protected" in out
    assert "stays local" in out


def test_a7_secret_content_still_exit_1(env, capsys):
    claude, *_ = env
    (claude / "agents" / "leak.md").write_text(
        "key = sk-ant-api03-" + "z" * 24 + "\n", encoding="utf-8")
    assert main(_argv(env, "collect")) == 1
    assert "REFUSED" in capsys.readouterr().out


def test_r6_status_clean_with_denied_note(env, capsys):
    claude, *_ = env
    (claude / "agents" / ".credentials.json").write_text("{}", encoding="utf-8")
    assert main(_argv(env, "status")) == 0
    out = capsys.readouterr().out
    assert "status: clean" in out
    assert "nothing to collect, nothing to apply" in out  # clean HOW
    assert "protected" in out
    assert "either direction" in out  # explains what protection means


def test_status_explains_what_was_compared(env, capsys):
    """"status: clean" alone told the user nothing (human-test finding
    2026-07-24). The report must show the three legs: what was compared,
    the checkout's branch/remote position, and the verdict."""
    assert main(_argv(env, "status")) == 0
    out = capsys.readouterr().out
    assert "compared" in out and "files across" in out
    assert "on main" in out  # humanized branch, not raw '## main...origin/main'
    assert "##" not in out


def test_no_color_and_non_tty_emit_no_escapes(env, capsys):
    """capsys is not a TTY, so output must already be plain; --no-color
    must also be accepted in both flag positions."""
    assert main(_argv(env, "status", "--no-color")) == 0
    out = capsys.readouterr().out
    assert "\033[" not in out
    assert main(["--no-color", *_argv(env, "status")]) == 0
    assert "\033[" not in capsys.readouterr().out


def test_flags_work_after_the_verb(env, capsys):
    """Human-test finding (2026-07-24): `ccs status --checkout-dir X` must
    work, not just the pre-verb global position."""
    claude, user, checkout, _, _ = env
    rc = main(["status", "--checkout-dir", str(checkout), "--claude-dir",
               str(claude), "--user-claude", str(user)])
    assert rc == 0
    assert "status: clean" in capsys.readouterr().out


def test_ccs_checkout_dir_env_var_honored(env, monkeypatch, capsys):
    """CCS_CHECKOUT_DIR removes the every-invocation --checkout-dir flag for
    a payload cloned outside user territory (symmetric with
    CLAUDE_CONFIG_DIR for the config territory)."""
    claude, user, checkout, _, _ = env
    monkeypatch.setenv("CCS_CHECKOUT_DIR", str(checkout))
    rc = main(["status", "--claude-dir", str(claude), "--user-claude", str(user)])
    assert rc == 0
    assert "status: clean" in capsys.readouterr().out


def test_checkout_dir_flag_beats_env_var(env, monkeypatch, capsys):
    """Precedence: --checkout-dir > CCS_CHECKOUT_DIR > default."""
    claude, user, checkout, _, _ = env
    monkeypatch.setenv("CCS_CHECKOUT_DIR", str(checkout / "does-not-exist"))
    rc = main(["status", "--checkout-dir", str(checkout), "--claude-dir",
               str(claude), "--user-claude", str(user)])
    assert rc == 0
    assert "status: clean" in capsys.readouterr().out


def test_missing_checkout_error_names_the_env_var(env, monkeypatch, capsys):
    claude, user, checkout, _, _ = env
    monkeypatch.setenv("CCS_CHECKOUT_DIR", str(checkout / "nope"))
    rc = main(["status", "--claude-dir", str(claude), "--user-claude", str(user)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "checkout not found" in err
    assert "CCS_CHECKOUT_DIR" in err


def test_module_entry_point_propagates_exit_code(env, tmp_path):
    """`python -m dazzle_claude_config` must honor the A7 contract. Without
    sys.exit(main()) in __main__.py it always exited 0, so scripted/CI use
    could never detect drift or errors (found 2026-07-24 while documenting
    payload creation). main() is called directly everywhere else in this
    suite, which is exactly why it went unnoticed -- so this test shells
    out to a real subprocess."""
    claude, user, checkout, _, _ = env
    common = ["--claude-dir", str(claude), "--user-claude", str(user), "--no-color"]
    clean = subprocess.run(
        [sys.executable, "-m", "dazzle_claude_config", "status",
         "--checkout-dir", str(checkout), *common],
        capture_output=True, text=True)
    assert clean.returncode == 0
    missing = subprocess.run(
        [sys.executable, "-m", "dazzle_claude_config", "status",
         "--checkout-dir", str(tmp_path / "nonexistent"), *common],
        capture_output=True, text=True)
    assert missing.returncode == 2, missing.stdout + missing.stderr


def test_empty_checkout_explains_how_to_seed_a_payload(env, tmp_path, capsys):
    """A freshly-created empty repo is the make-a-payload case, not the
    wrong-repo case -- the error must say what to do next."""
    claude, user, _, _, _ = env
    empty = tmp_path / "brand-new-repo"
    empty.mkdir()
    rc = main(["status", "--checkout-dir", str(empty), "--claude-dir", str(claude),
               "--user-claude", str(user)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "empty repo" in err
    assert "ccs collect" in err
    assert "mkdir skills" in err


def test_collect_then_status_clean(env):
    claude, *_ = env
    (claude / "agents" / "newbie.md").write_text("new\n", encoding="utf-8")
    assert main(_argv(env, "collect")) == 0
    assert main(_argv(env, "status")) == 0


def test_apply_flow_and_diff(env, capsys):
    claude, user, checkout, _, _ = env
    (checkout / "dotclaude" / "agents" / "fleet.md").write_text("fleet\n",
                                                                encoding="utf-8")
    assert main(_argv(env, "diff")) == 1
    assert "repo-only" in capsys.readouterr().out
    assert main(_argv(env, "apply")) == 0
    assert (claude / "agents" / "fleet.md").exists()
    assert main(_argv(env, "status")) == 0


def test_setup_gitopssafetyerror_from_checkoutrepo_exit2(tmp_path, monkeypatch, capsys):
    """_setup() only wraps CheckoutRepo() in `except GitError` (falling back
    to repo=None for plain-directory checkouts); GitopsSafetyError (the
    home-repo guard) is a SIBLING exception class, not a GitError subclass,
    so it is NOT caught there. Confirm it still propagates up to main()'s
    outer `except (ManifestError, GitError, GitopsSafetyError)` and exits
    cleanly (2, with a message) instead of crashing with a traceback. This
    exact path (inline construction inside _setup(), not a pre-built repo
    object passed into apply()) had no prior test coverage."""
    fake_home = tmp_path / "fake_home"
    (fake_home / ".claude").mkdir(parents=True)
    git(fake_home, "init", "-q", "-b", "main")
    (fake_home / "ccs-manifest.json").write_text(
        json.dumps({"manifest_version": 1, "territories": {}, "entries": []}),
        encoding="utf-8")
    git(fake_home, "add", "-A")
    git(fake_home, "commit", "-q", "-m", "seed")
    monkeypatch.setattr("dazzle_claude_config.gitops.Path.home",
                        staticmethod(lambda: fake_home))
    rc = main(["--checkout-dir", str(fake_home), "--claude-dir",
               str(fake_home / ".claude"), "--user-claude", str(tmp_path / "user"),
               "status"])
    assert rc == 2
    assert "home" in capsys.readouterr().err.lower()
