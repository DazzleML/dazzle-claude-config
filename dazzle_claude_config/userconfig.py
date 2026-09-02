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

import dataclasses
import json
import os
from dataclasses import dataclass
from pathlib import Path

#: The ONE sentence ccs uses for a config file that parses but cannot be a
#: config, and the ONE function that phrases a parse failure. Both are here,
#: as data and as a function, because they were written out separately at each
#: site and then had to agree by hand -- which they did not: `doctor` and
#: `setup update` said "not valid JSON" while `status`, `apply` and `collect`
#: printed `JSONDecodeError` verbatim, so the same broken file was described
#: two ways depending on which verb you happened to run.
#:
#: The two cases stay distinct on purpose. `[1, 2, 3]` IS valid JSON and still
#: cannot be a config; calling that "not valid JSON" sends someone hunting for
#: a syntax error that is not there.
NOT_AN_OBJECT = "the top level is not a JSON object -- it must be { ... }"


def not_valid_json(exc) -> str:
    """How ccs describes a file the JSON parser rejected."""
    return f"not valid JSON ({exc})"


#: Where the prose lives, inside the installed package. Shipping it as package
#: DATA rather than only as a web page is the point: `--explain <setting>` must
#: answer on a VPS over SSH, with no browser, no config file and no network.
EXPLANATIONS_FILE = "settings-explanations.json"

#: The generated reference `--explain` points at when asked for everything at
#: once. Generated FROM the file above, so it cannot describe a ccs that never
#: shipped -- the reason a hand-written page was rejected in the first place.
DOCS_URL = ("https://github.com/DazzleML/dazzle-claude-config"
            "/blob/main/docs/configuration.md")


@dataclass(frozen=True)
class Key:
    """One setting's BEHAVIOUR -- what it defaults to, where it can be set,
    and which values are legal.

    What it MEANS lives in `EXPLANATIONS_FILE` beside this module and is
    attached at import, so the prose can be hand-edited and generated from
    without touching Python. The split is deliberate and one-directional:
    behaviour never leaves the code. A packaging slip that loses the JSON must
    leave ccs *unexplained*, never *undefaulted* -- an unexplained setting is
    an annoyance, a missing default is a machine quietly behaving differently.
    """
    default: object
    env: str
    choices: frozenset[str] | None = None
    explain: str = ""  # attached from the packaged JSON; see load_explanations


def load_explanations() -> tuple[dict[str, str], str | None]:
    """Read the packaged explanations. Returns (texts, reason-it-failed).

    Every failure degrades rather than raising, for the reason in `Key`: ccs
    must still run correctly with no prose at all. `ccs doctor` surfaces the
    reason, so a packaging slip is visible instead of silently producing an
    `--explain` that says nothing.
    """
    try:
        from importlib import resources
        raw = (resources.files("dazzle_claude_config")
               .joinpath(EXPLANATIONS_FILE).read_text(encoding="utf-8"))
    except (OSError, ModuleNotFoundError, TypeError) as exc:
        return {}, f"{EXPLANATIONS_FILE} is not installed ({exc})"
    try:
        body = json.loads(raw)
    except ValueError as exc:
        return {}, f"{EXPLANATIONS_FILE} is not valid JSON ({exc})"
    texts = body.get("settings") if isinstance(body, dict) else None
    if not isinstance(texts, dict):
        return {}, f"{EXPLANATIONS_FILE} has no 'settings' object"
    return {k: v for k, v in texts.items() if isinstance(v, str)}, None


def explanation_gaps(keys, texts) -> tuple[set[str], set[str]]:
    """(settings with no explanation, explanations for no setting).

    While the prose was a constructor argument, a setting could not be added
    without one -- it was a TypeError at import and the program would not
    start. Prose in a separate file cannot give that, so this is its
    replacement, and it is a PURE function precisely so the detection can be
    tested against synthetic input. Mutation L4 is why that distinction
    matters: proving the current settings happen to be explained is far weaker
    than proving an unexplained one gets caught.
    """
    named = set(keys)
    explained = {n for n, t in texts.items() if (t or "").strip()}
    return named - explained, explained - named


EXPLANATIONS, EXPLANATIONS_ERROR = load_explanations()


def _attach_explanations(raw: dict[str, Key]) -> dict[str, Key]:
    """Fold the packaged prose onto the behaviour table, so every consumer --
    `--explain`, `doctor`, the docs generator -- reads one lookup."""
    return {name: dataclasses.replace(k, explain=EXPLANATIONS.get(name, ""))
            for name, k in raw.items()}


#: Every setting ccs honours. Chosen so a fresh machine behaves safely without
#: a config file at all: prompt rather than guess, and never spend money
#: without being asked.
KEYS: dict[str, Key] = _attach_explanations({
    "on_divergence": Key(
        default="prompt", env="CCS_ON_DIVERGENCE",
        choices=frozenset({"prompt", "skip", "force"})),

    "difftool": Key(default=None, env="CCS_DIFFTOOL"),

    "ai_merge_command": Key(default=None, env="CCS_AI_MERGE_COMMAND"),

    "interactive": Key(default=True, env="CCS_INTERACTIVE"),

    "status_detail": Key(
        default="auto", env="CCS_STATUS_DETAIL",
        choices=frozenset({"auto", "long", "compact"})),

    "status_max_lines": Key(default=30, env="CCS_STATUS_MAX_LINES"),

    "fetch": Key(default=True, env="CCS_FETCH"),

    "fetch_timeout": Key(default=15, env="CCS_FETCH_TIMEOUT"),

    "require_current": Key(default=False, env="CCS_REQUIRE_CURRENT"),

    "auto_pull": Key(default=False, env="CCS_AUTO_PULL"),

    "sync_removals": Key(
        default="untouched", env="CCS_SYNC_REMOVALS",
        choices=frozenset({"untouched", "all", "never"})),

    "merge_inject": Key(
        default="ask", env="CCS_MERGE_INJECT",
        choices=frozenset({"ask", "always", "never"})),
})

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
                errors.append(f"{path}: {NOT_AN_OBJECT}")
            else:
                for k, v in data.items():
                    if k in DEFAULTS:
                        cfg[k] = v
                    else:
                        errors.append(f"{path}: unknown key {k!r} (ignored)")
        except json.JSONDecodeError as e:
            # The reader's words, not the parser's. This path is the OLDEST of
            # the three that report a broken config -- `doctor` and
            # `setup update` were given plain language and this one was left
            # printing `JSONDecodeError:` verbatim, so the same file produced
            # two different qualities of message depending on which verb you
            # happened to run. Found by a tester cross-checking three
            # checklists against each other, which is the only way that kind
            # of inconsistency surfaces.
            errors.append(f"{path}: {not_valid_json(e)}")
        except OSError as e:
            errors.append(f"{path}: cannot be read ({e.strerror or e})")

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

    @property
    def unreadable_reason(self) -> str | None:
        """The reason, ready to print. Now simply `unreadable` itself.

        This used to RE-DERIVE the sentence: the field held the parser's own
        `JSONDecodeError: ...` and this property translated it by sniffing for
        a substring. That meant the wording existed twice -- here and again in
        `load()` -- and the two had to agree by hand. They did not, which is
        the defect this release fixed, so leaving the second copy in place
        would have rebuilt the trap while claiming to have removed it.

        `plan_config` now stores the sentence ccs will actually print, built
        by `not_valid_json()` / `NOT_AN_OBJECT`, so there is one definition and
        nothing to translate. The property stays because callers read it and
        it names the intent, not because it does any work.
        """
        return self.unreadable


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
                          unreadable=not_valid_json(e))
    if not isinstance(data, dict):
        return ConfigPlan(path=path, exists=True, missing={}, unknown=[],
                          unreadable=NOT_AN_OBJECT)
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
