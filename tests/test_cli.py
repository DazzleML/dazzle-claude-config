"""CLI: A7 exit-code contract (0 clean / 1 drift / 2 error) via main(argv)."""
from dazzle_claude_config.cli import main


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


def test_a7_collect_refusal_exit_1(env, capsys):
    claude, *_ = env
    (claude / "agents" / ".credentials.json").write_text("{}", encoding="utf-8")
    assert main(_argv(env, "collect")) == 1
    assert "REFUSED" in capsys.readouterr().out


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
