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
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Key:
    """One setting, with everything ccs knows about it in one place.

    `explain` used to be a comment above the entry in DEFAULTS. Comments are
    invisible to the program, so "what does this key mean?" could only be
    answered by reading source -- and every proposal for fixing that (a docs
    page, a generated sidecar, a parallel dict) was a COPY, which drifts. The
    text was already one block per key; it was a table written as comments.
    Making it data means `ccs setup update --explain`, `ccs doctor`, and any
    future generated documentation all read the same words, and a key added
    without an explanation fails a test instead of shipping unexplained.
    """
    default: object
    env: str
    explain: str
    choices: frozenset[str] | None = None


#: Every setting ccs honours. Chosen so a fresh machine behaves safely without
#: a config file at all: prompt rather than guess, and never spend money
#: without being asked.
KEYS: dict[str, Key] = {
    "on_divergence": Key(
        default="prompt", env="CCS_ON_DIVERGENCE",
        choices=frozenset({"prompt", "skip", "force"}),
        explain="What to do when a file has diverged two ways. 'prompt' asks; "
                "'skip' leaves it alone and reports it; 'force' overwrites, "
                "which is destructive."),

    "difftool": Key(
        default=None, env="CCS_DIFFTOOL",
        explain="The git difftool to force for `ccs diff` and `ccs merge`. "
                "Unset means resolve it from your git config, falling back to "
                "scanning for one when the configured default is not on disk."),

    "ai_merge_command": Key(
        default=None, env="CCS_AI_MERGE_COMMAND",
        explain="Shell command for AI-assisted merge; unset disables the "
                "option entirely. It receives the base, your version and the "
                "output path as $CCS_BASE, $CCS_OURS and $CCS_OUT. Left unset "
                "on purpose: AI costs money, so it is opt-in per machine."),

    "interactive": Key(
        default=True, env="CCS_INTERACTIVE",
        explain="Whether ccs may ask you questions. False suppresses every "
                "prompt and uses on_divergence directly. Also inferred "
                "automatically when input is not a terminal, so an automated "
                "run never hangs waiting for an answer nobody can give."),

    "status_detail": Key(
        default="auto", env="CCS_STATUS_DETAIL",
        choices=frozenset({"auto", "long", "compact"}),
        explain="How much detail `ccs status` prints. 'auto' shows the "
                "per-file breakdown until it would exceed status_max_lines, "
                "then falls back to one line per entry; 'long' always shows "
                "every file; 'compact' always shows one line per entry."),

    "status_max_lines": Key(
        default=30, env="CCS_STATUS_MAX_LINES",
        explain="The line budget 'auto' spends before collapsing to one line "
                "per entry. Raise it if you would rather always see every "
                "file; lower it on a small terminal."),

    "fetch": Key(
        default=True, env="CCS_FETCH",
        explain="Whether `ccs status` contacts the remote before reporting. "
                "It does, so that 'in sync with origin/main' is a claim about "
                "the remote rather than about whenever you last fetched. It "
                "touches remote-tracking refs only -- no branch, index or "
                "working tree. Turn it off for an air-gapped or metered "
                "machine; --no-fetch does the same for one run."),

    "fetch_timeout": Key(
        default=15, env="CCS_FETCH_TIMEOUT",
        explain="Seconds before a fetch is treated as failed and the pull "
                "state is reported as unknown. One repo and one remote: 15 is "
                "generous for a hosted remote and short enough that a dead "
                "network does not make `status` feel hung."),

    "require_current": Key(
        default=False, env="CCS_REQUIRE_CURRENT",
        explain="What happens when your checkout is behind its remote. By "
                "default apply and collect warn and proceed -- 'install what I "
                "have here, now' is a legitimate intent and nothing is lost. "
                "True turns that warning into a refusal, for people who want "
                "the pull-first loop enforced. --require-current does it for "
                "one run."),

    "auto_pull": Key(
        default=False, env="CCS_AUTO_PULL",
        explain="Whether `ccs status` fast-forwards your checkout before "
                "reporting, when the fetch finds it behind and the move is "
                "safe. Strictly fast-forward only: a diverged branch or a file "
                "in the way is reported, never merged, rebased or stashed. "
                "status only -- apply and collect keep their warning, see "
                "require_current. --pull / --no-pull per run."),

    "sync_removals": Key(
        default="untouched", env="CCS_SYNC_REMOVALS",
        choices=frozenset({"untouched", "all", "never"}),
        explain="What to do about a file the payload RETIRED that this machine "
                "still carries. 'untouched' moves it into the backup directory "
                "only when your copy still matches a committed version, i.e. "
                "holds nothing of yours; a copy you edited is reported and "
                "kept instead, because staging away work the payload never had "
                "is the one thing this tool refuses to do quietly. 'all' "
                "stages every retired file; 'never' only reports. Nothing is "
                "ever deleted in place."),
}

#: Derived from KEYS so there is exactly one place per setting. Kept as
#: module-level names because seven call sites in this file read them, and
#: the point of the table was to change no reader.
DEFAULTS = {name: k.default for name, k in KEYS.items()}
ENV_MAP = {name: k.env for name, k in KEYS.items()}

VALID_ON_DIVERGENCE = KEYS["on_divergence"].choices
VALID_STATUS_DETAIL = KEYS["status_detail"].choices
VALID_SYNC_REMOVALS = KEYS["sync_removals"].choices


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
            # utf-8-SIG: this file is meant to be hand-edited, and the
            # Windows editors people reach for write a BOM. Reading it
            # as plain utf-8 made every setting silently revert to its
            # default -- auto_pull: true read as False -- with the
            # decode error recorded somewhere nothing printed it. The
            # other three user-territory records already tolerate a BOM.
            data = json.loads(path.read_text(encoding="utf-8-sig"))
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

    # An unrecognised removal policy falls back to the SAFEST value, not the
    # default one: a typo must never widen what the tool deletes on your
    # behalf. "never" only reports, so a misspelling costs a manual step
    # rather than a file staged away.
    if cfg["sync_removals"] not in VALID_SYNC_REMOVALS:
        errors.append(
            f"sync_removals={cfg['sync_removals']!r} is not one of "
            f"{sorted(VALID_SYNC_REMOVALS)}; using 'never' (report only)")
        cfg["sync_removals"] = "never"

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


@dataclass
class ConfigPlan:
    """What `ccs setup update` would do to this machine's config file.

    Computed before anything is written so `--dry-run` and the real run share
    one answer rather than two code paths that can disagree.
    """
    path: Path
    exists: bool
    missing: dict[str, object]      # known to this ccs, absent from the file
    unknown: list[str]              # in the file, unknown to this ccs
    unreadable: str | None = None   # the file is there but will not parse

    @property
    def would_write(self) -> bool:
        return self.unreadable is None and (not self.exists or bool(self.missing))

    @property
    def needs_a_human(self) -> bool:
        """Things ccs will not decide: a broken file, or a key it cannot judge.

        An unknown key is NOT this. It is reported and left alone, which is a
        complete and correct outcome -- it usually means a newer ccs wrote the
        file, and removing it would destroy a setting that ccs will want back.
        """
        return self.unreadable is not None


def plan_config(user_claude: Path | None = None) -> ConfigPlan:
    """Compare the file on disk against the keys this version knows.

    Additive only, by construction: this reports what is MISSING and never
    looks at the value of a key that is present. A key the user set to the
    default value is indistinguishable from one ccs wrote, and both must be
    left alone -- the rule is about provenance, not equality.
    """
    path = config_path(user_claude)
    if not path.is_file():
        return ConfigPlan(path=path, exists=False, missing=dict(DEFAULTS),
                          unknown=[])
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        return ConfigPlan(path=path, exists=True, missing={}, unknown=[],
                          unreadable=f"{type(e).__name__}: {e}")
    if not isinstance(data, dict):
        return ConfigPlan(path=path, exists=True, missing={}, unknown=[],
                          unreadable="the file is not a JSON object")
    present = set(data)
    return ConfigPlan(
        path=path, exists=True,
        missing={k: v for k, v in DEFAULTS.items() if k not in present},
        unknown=sorted(k for k in present if k not in DEFAULTS
                       and not k.startswith("_")))


def apply_config_plan(plan: ConfigPlan) -> Path:
    """Add the missing keys at their defaults. Touches nothing else.

    Every existing key keeps its value, its spelling and -- because the file
    is re-serialised from a dict that preserves insertion order -- its
    position, with the new keys appended after it.
    """
    if plan.unreadable is not None:
        raise ValueError(f"refusing to write over an unreadable config: "
                         f"{plan.unreadable}")
    body: dict = {}
    if plan.exists:
        body = json.loads(plan.path.read_text(encoding="utf-8-sig"))
    body.update(plan.missing)
    plan.path.parent.mkdir(parents=True, exist_ok=True)
    plan.path.write_text(json.dumps(body, indent=2) + "\n",
                         encoding="utf-8", newline="\n")
    return plan.path
