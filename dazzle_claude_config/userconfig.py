"""User preferences for ccs behaviour -- distinct from the sync manifest.

The manifest answers "what syncs where"; this answers "how should ccs behave
when it needs a decision". Keeping them apart matters: the manifest is payload
that travels between machines, while these are per-machine preferences that
should NOT travel (one box has a GUI diff tool, another is a headless VPS).

Resolution order, first hit wins:
    explicit argument  >  environment variable  >  config file  >  built-in

Lives in user territory (`~/claude/ccs-config.json`), not `~/.claude` -- that
directory is Claude Code's and gets swept during upgrades.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

#: Built-in defaults. Chosen so a fresh machine behaves safely without a config
#: file: prompt rather than guess, and never spend money without being asked.
DEFAULTS = {
    # prompt | skip | force -- what to do when a file has diverged two ways.
    # "skip" leaves it alone and reports; "force" overwrites (destructive).
    "on_divergence": "prompt",

    # git difftool name to force. None -> resolve from git config, with a
    # fallback scan when the configured default does not exist on disk.
    "difftool": None,

    # Shell command for AI-assisted merge. None disables the option entirely.
    # Receives BASE, OURS and OUT paths as $CCS_BASE/$CCS_OURS/$CCS_OUT.
    # Left unset on purpose: AI costs money, so it is opt-in per machine.
    "ai_merge_command": None,

    # False suppresses every prompt and uses on_divergence directly. Also
    # inferred automatically when stdin is not a TTY, so CI never hangs.
    "interactive": True,

    # auto | long | compact -- how much detail `ccs status` prints.
    # "auto" shows the per-file breakdown until it would exceed
    # status_max_lines, then falls back to one line per entry.
    "status_detail": "auto",

    # The line budget "auto" spends before collapsing. Raise it if you would
    # rather always see every file; lower it on a small terminal.
    "status_max_lines": 30,

    # Fetch the upstream before reading the branch line, so "in sync with
    # origin/main" is a claim about the remote and not about the last fetch.
    # Touches remote-tracking refs only. False for air-gapped or metered
    # machines; --no-fetch does the same for one run.
    "fetch": True,

    # Seconds before a fetch is treated as failed ("pull status unknown").
    # One repo, one remote: 15 s is generous for GitHub and short enough
    # that a dead network does not make `status` feel hung.
    "fetch_timeout": 15,

    # When the checkout is behind its upstream, apply/collect WARN and proceed
    # by default -- "install what I have here, now" is a legitimate intent and
    # nothing is lost. True turns the warning into a refusal (exit 1) for
    # users who want the pull-first loop enforced. --require-current per run.
    "require_current": False,

    # True lets `status` close the loop it used to describe: when the fetch
    # finds the checkout behind AND fast-forwardable, status fast-forwards
    # first and then reports the real drift, saying it did. Strictly ff-only:
    # a divergent branch or a dirty file in the way is reported, never
    # merged, rebased, or stashed. status only -- apply/collect keep their
    # warning (see require_current). --pull / --no-pull per run.
    "auto_pull": False,
}

ENV_MAP = {
    "on_divergence": "CCS_ON_DIVERGENCE",
    "difftool": "CCS_DIFFTOOL",
    "ai_merge_command": "CCS_AI_MERGE_COMMAND",
    "interactive": "CCS_INTERACTIVE",
    "status_detail": "CCS_STATUS_DETAIL",
    "status_max_lines": "CCS_STATUS_MAX_LINES",
    "fetch": "CCS_FETCH",
    "fetch_timeout": "CCS_FETCH_TIMEOUT",
    "require_current": "CCS_REQUIRE_CURRENT",
    "auto_pull": "CCS_AUTO_PULL",
}

VALID_ON_DIVERGENCE = {"prompt", "skip", "force"}
VALID_STATUS_DETAIL = {"auto", "long", "compact"}


def config_path(user_claude: Path | None = None) -> Path:
    if user_claude is None:
        user_claude = Path(os.path.expanduser("~")) / "claude"
    return Path(user_claude) / "ccs-config.json"


def _coerce(key: str, raw: str):
    if key in ("interactive", "fetch", "require_current"):
        return str(raw).strip().lower() not in ("0", "false", "no", "off")
    if key == "fetch_timeout":
        try:
            return max(1, int(str(raw).strip()))
        except (TypeError, ValueError):
            return DEFAULTS["fetch_timeout"]
    if key == "status_max_lines":
        # Environment values arrive as strings; a bad one must not crash
        # `status`, so fall back to the built-in rather than raising.
        try:
            return max(1, int(str(raw).strip()))
        except (TypeError, ValueError):
            return DEFAULTS["status_max_lines"]
    return raw


def load(user_claude: Path | None = None, overrides: dict | None = None) -> dict:
    """Merge built-ins, config file, environment, and explicit overrides.

    A malformed config file is reported through the returned `_errors` list
    rather than raised: a broken preferences file must not stop a sync, but it
    must never be silently ignored either.
    """
    cfg = dict(DEFAULTS)
    errors: list[str] = []

    path = config_path(user_claude)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                errors.append(f"{path}: expected a JSON object")
            else:
                for k, v in data.items():
                    if k in DEFAULTS:
                        cfg[k] = v
                    else:
                        errors.append(f"{path}: unknown key {k!r} (ignored)")
        except (OSError, json.JSONDecodeError) as e:
            errors.append(f"{path}: {e.__class__.__name__}: {e}")

    for key, env in ENV_MAP.items():
        raw = os.environ.get(env)
        if raw is not None:
            cfg[key] = _coerce(key, raw)

    for k, v in (overrides or {}).items():
        if v is not None:
            cfg[k] = v

    if cfg["on_divergence"] not in VALID_ON_DIVERGENCE:
        errors.append(
            f"on_divergence={cfg['on_divergence']!r} is not one of "
            f"{sorted(VALID_ON_DIVERGENCE)}; using 'prompt'")
        cfg["on_divergence"] = "prompt"

    # A non-TTY can never answer a prompt. Downgrade rather than hang, and say
    # so, because a run that silently changed policy is worse than a slow one.
    if cfg["interactive"] and not _stdin_is_tty():
        cfg["interactive"] = False
        cfg["_downgraded_interactive"] = True

    cfg["_errors"] = errors
    cfg["_path"] = str(path)
    return cfg


def _stdin_is_tty() -> bool:
    import sys
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


def write_template(user_claude: Path | None = None) -> Path:
    """Write a commented starter config. Never overwrites an existing file."""
    path = config_path(user_claude)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {k: v for k, v in DEFAULTS.items()}
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return path
