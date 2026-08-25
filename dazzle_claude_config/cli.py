"""ccs command-line interface.

Exit codes (A7): 0 = clean/success, 1 = drift or refusals present, 2 = error.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
from pathlib import Path

from . import _version, merge, render
from .apply import ApplyConflictError, apply
from .collect import collect
from .gitops import CheckoutRepo, GitError, GitopsSafetyError
from .manifest import Manifest, ManifestError
from .platform_info import default_checkout_dir, territory_roots
from .render import c
from . import boxconfig, userconfig
from .syncmap import (_normalize_eol, diff_all, entry_gate_reason, files_differ,
                      line_stats, only_scope, rel_in_scope)

EXIT_CLEAN, EXIT_DRIFT, EXIT_ERROR = 0, 1, 2


def _add_common(parser, suppress=False):
    """Common options, accepted BOTH before and after the verb. Humans
    naturally type `ccs status --checkout-dir X`; argparse globals only
    work pre-verb, so each subparser gets SUPPRESS-default copies that
    override the global value only when actually provided."""
    d = argparse.SUPPRESS if suppress else None
    parser.add_argument("--checkout-dir", default=d,
                        help="payload checkout location (also honors CCS_CHECKOUT_DIR; "
                             "default: ~/claude/dazzle-claude-code-config)")
    parser.add_argument("--claude-dir", default=d,
                        help="override ~/.claude (also honors CLAUDE_CONFIG_DIR)")
    parser.add_argument("--user-claude", default=d, help="override ~/claude")
    parser.add_argument("--no-color", action="store_true",
                        default=argparse.SUPPRESS if suppress else False,
                        help="plain output, no ANSI color (NO_COLOR is honored too)")
    parser.add_argument("--no-fetch", action="store_true",
                        default=argparse.SUPPRESS if suppress else False,
                        help="do not fetch the upstream first; the branch line then "
                             "reflects the last fetch, and says so (config: fetch)")


# The `git` verb splits argv BEFORE argparse ever runs (the dazzlecmd
# dispatch_tool pattern, and a candidate for dazzle-clilib extraction):
# everything after the literal token `git` belongs to git, verbatim --
# including tokens that look like ccs flags. Only these true globals may
# appear before the verb; the two tuples are cross-checked against the
# parser by a test so they cannot drift from _add_common.
_VALUE_GLOBALS = ("--checkout-dir", "--claude-dir", "--user-claude")
_FLAG_GLOBALS = ("--no-color", "--no-fetch")


def _split_git_passthrough(argv: list[str]):
    """(seen_globals, git_args) when this argv is a `git` passthrough run,
    else None (let argparse have it). Never consumes anything after `git`."""
    seen: dict[str, str | bool] = {}
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "git":
            return seen, argv[i + 1:]
        if tok in _VALUE_GLOBALS:
            if i + 1 < len(argv):
                seen[tok] = argv[i + 1]
            i += 2
            continue
        head, _, val = tok.partition("=")
        if head in _VALUE_GLOBALS and val:
            seen[head] = val
            i += 1
            continue
        if tok in _FLAG_GLOBALS:
            seen[tok] = True
            i += 1
            continue
        return None  # another verb, -h, --version, or a typo: argparse's job
    return None


def _run_git_verb(seen: dict, git_args: list[str]) -> int:
    """`ccs git <anything>` == `git -C <resolved checkout> <anything>`.

    The checkout resolves exactly as for every other verb; validation goes
    through CheckoutRepo so the home-repo guards (A4) hold here too. Args
    and stdio pass through untouched -- pagers, prompts, and credential
    helpers behave as if the user had cd'd there -- and git's exit code is
    ccs's exit code.
    """
    render.init(bool(seen.get("--no-color")))
    co = seen.get("--checkout-dir")
    checkout = Path(co).expanduser().resolve() if co else default_checkout_dir()
    if not checkout.is_dir():
        # Same pre-check _setup() gives every other verb. Without it,
        # CheckoutRepo's rev-parse probe hands subprocess a nonexistent
        # cwd, which raises NotADirectoryError (WinError 267) -- an
        # OSError, not a GitError -- and the user gets a traceback instead
        # of an answer (found by the v0.5.1 checklist run).
        print(c("bold_red", "error") + f": checkout not found: {checkout}",
              file=sys.stderr)
        return EXIT_ERROR
    try:
        CheckoutRepo(checkout)
    except (GitError, GitopsSafetyError) as e:
        print(c("bold_red", "error") + f": {e}", file=sys.stderr)
        return EXIT_ERROR
    if not git_args:
        print(f"{c('bold', 'checkout')}  {c('cyan', str(checkout))}")
        print(c("dim", "ccs git <anything> runs git there, from anywhere: "
                       "ccs git pull / ccs git push / ccs git log --oneline"))
        return EXIT_CLEAN
    return subprocess.call(["git", "-C", str(checkout), *git_args])


def _seed_verb(args, manifest, checkout, roots, repo) -> int:
    """`ccs seed keep|reset|list` -- record, revoke, or show the per-box
    answer to "yours or the payload's?" for seeded files (issue #27)."""
    from . import seeddecisions
    from .syncmap import entry_bases
    user_claude = roots.get("USER_CLAUDE")
    if args.action == "list":
        findings, errors = _seed_findings(
            manifest, checkout, roots, repo, args._box_tags, user_claude)
        _print_seed_block(findings, errors, long_form=True)
        if not findings:
            print(c("dim", "no file seed entries apply to this box"))
        return EXIT_CLEAN
    target = args.target
    if not target:
        print(c("bold_red", "error") + f": seed {args.action} needs a target "
              "(the entry's target or repo path, e.g. CLAUDE.md)",
              file=sys.stderr)
        return EXIT_ERROR
    norm_t = target.replace(chr(92), "/")
    entry = next((e for e in manifest.seed_entries()
                  if norm_t in (e.target, e.repo)), None)
    if entry is None:
        print(c("bold_red", "error") + f": {target!r} is not a seed entry "
              "(ccs seed list shows them)", file=sys.stderr)
        return EXIT_ERROR
    if args.action == "reset":
        if seeddecisions.reset(entry.target, user_claude):
            print(f"forgot the decision for {c('cyan', entry.target)} -- "
                  "status will ask again if it differs from the seed")
        else:
            print(c("dim", f"no decision recorded for {entry.target}"))
        return EXIT_CLEAN
    # keep
    _live, repo_base = entry_bases(entry, checkout, roots, manifest.territories)
    if not repo_base.is_file():
        print(c("bold_red", "error") + f": the seed for {entry.target} is not "
              "a file in the checkout", file=sys.stderr)
        return EXIT_ERROR
    mode = "always" if args.always else "until-changed"
    blob = _norm_sha(repo_base.read_bytes())
    path = seeddecisions.keep(entry.target, mode, blob, user_claude)
    tail = ("for good -- status stays quiet about it" if mode == "always" else
            "until the payload's seed changes -- then status asks again")
    print(f"kept: {c('cyan', entry.target)} is yours {tail}")
    print(c("dim", f"recorded in {path} (hand-editable; ccs seed reset revokes)"))
    return EXIT_CLEAN


def _setup_box(args) -> int:
    """`ccs setup box` -- declare this box's identity (name + tags), the
    file the tags gate and the probe read. Never overwrites (#26 slice 1)."""
    user_claude = Path(args.user_claude).expanduser() if args.user_claude \
        else None
    existing = boxconfig.box_path(user_claude)
    if existing.exists():
        box = boxconfig.load(user_claude)
        for err in box.errors:
            print(c("yellow", f"warning: {err}"))
        tags = ", ".join(sorted(box.tags)) or "none"
        print(f"{c('bold', 'declared')}  {c('cyan', str(existing))}")
        print(f"          name {c('cyan', box.name or '(unset)')}, "
              f"tags {c('cyan', tags)}")
        print(c("dim", "already declared -- edit the file to change it; "
                       "this command never overwrites"))
        return EXIT_CLEAN
    name = args.name
    if not name and sys.stdin.isatty():
        try:
            name = input("box name (lowercase, a declared name -- "
                         "not a hostname): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return EXIT_ERROR
    if not name:
        print(c("bold_red", "error") + ": pass --name (non-interactive run)",
              file=sys.stderr)
        return EXIT_ERROR
    tags = list(args.tag or [])
    if name not in tags:
        tags.insert(0, name)
    path, errs = boxconfig.write_box(name, tags, user_claude)
    if errs:
        for e in errs:
            print(c("bold_red", "error") + f": {e}", file=sys.stderr)
        return EXIT_ERROR
    print(f"declared: box {c('cyan', name)}, tags "
          f"{c('cyan', ', '.join(tags))} -> {path}")
    print(c("dim", "tag-gated entries now apply and collect only where "
                   "every tag is declared; run ccs status --long to see "
                   "what this box is (and is not) offered"))
    return EXIT_CLEAN


def _doctor(args) -> int:
    """`ccs doctor` -- read-only environment verification (#26 slice 2).

    Safe on a read-only-policy box: prints findings and fixes, does
    nothing. Exit 0 all OK, 1 warnings, 2 failures (the status pattern).
    """
    findings: list[tuple[str, str]] = []   # (level, line)

    def ok(line):
        findings.append(("OK", line))

    def warn(line):
        findings.append(("WARN", line))

    def fail(line):
        findings.append(("FAIL", line))

    # interpreter + git
    v = sys.version_info
    (ok if v >= (3, 10) else fail)(f"python {v.major}.{v.minor}.{v.micro}"
                                   + ("" if v >= (3, 10) else " -- 3.10+ required"))
    import shutil as _sh
    (ok if _sh.which("git") else fail)("git on PATH"
                                       if _sh.which("git") else
                                       "git not found on PATH -- install git")
    # checkout
    co = Path(args.checkout_dir).expanduser().resolve() if args.checkout_dir \
        else default_checkout_dir()
    repo = None
    if not co.is_dir():
        fail(f"checkout not found: {co} -- git clone your payload there, "
             "set CCS_CHECKOUT_DIR, or pass --checkout-dir")
    else:
        try:
            repo = CheckoutRepo(co)
            ok(f"checkout {co}")
        except (GitError, GitopsSafetyError) as e:
            fail(f"checkout: {e}")
    user_claude = Path(args.user_claude).expanduser() if args.user_claude \
        else None
    cfg = userconfig.load(user_claude)
    if repo is not None:
        url = repo.remote_url()
        (ok if url else warn)(f"remote {render.remote_host(url)}" if url else
                              "no remote named origin -- pushes and pulls "
                              "have nowhere to go")
        if url and cfg.get("fetch", True) and not getattr(args, "no_fetch", False):
            fetched, why = repo.fetch(timeout=int(cfg.get("fetch_timeout", 15)))
            if fetched is True:
                ok("remote reachable (fetched)")
            elif fetched is None:
                warn("no upstream tracking branch -- git push -u origin <branch>")
            else:
                warn(f"fetch failed: {why} -- offline, or credentials needed")
    # box identity
    bp = boxconfig.box_path(user_claude)
    if not bp.exists():
        warn(f"no box file at {bp} -- tag-gated entries will not apply; "
             "ccs setup box creates it")
    else:
        box = boxconfig.load(user_claude)
        for e in box.errors:
            warn(f"box file: {e}")
        if not box.errors:
            tags = ", ".join(sorted(box.tags)) or "none"
            ok(f"box {box.name or '(unnamed)'} (tags: {tags})")
    # user config
    cfg_path = userconfig.config_path(user_claude)
    if cfg_path.exists():
        try:
            import json as _json
            _json.loads(cfg_path.read_text(encoding="utf-8-sig"))
            ok(f"user config {cfg_path.name}")
        except ValueError as e:
            warn(f"{cfg_path.name}: not valid JSON ({e}) -- defaults in effect")
    # manifest + seeds
    manifest = None
    if co.is_dir():
        try:
            manifest = Manifest.load(co)
            ok(f"manifest: {len(manifest.entries)} entries")
        except ManifestError as e:
            if "manifest not found" in str(e):
                manifest = Manifest.implicit(co)
                warn(f"no ccs-manifest.json -- implicit ~/.claude layout "
                     f"({len(manifest.entries)} surfaces)")
            else:
                fail(f"manifest: {e}")
    if manifest is not None:
        roots = territory_roots(args.claude_dir, args.user_claude)
        box = boxconfig.load(user_claude)
        sf, serr = _seed_findings(manifest, co, roots, repo, box.tags,
                                  roots.get("USER_CLAUDE"))
        for e in serr:
            warn(e)
        for t, s, live, repo_p in sf:
            if s in _SEED_ACTIONABLE:
                _tone, msg = _SEED_ACTIONABLE[s]
                warn(f"seeded {t}: " + msg.format(t=t) + "\n"
                     + _seed_paths_line(live, repo_p, indent="       "))
        probe = co / "scripts" / "probe_layers.py"
        if probe.is_file():
            findings.append(("info", f"payload ships a session verifier -- "
                                     f"python {probe}"))
    tones = {"OK": "green", "WARN": "yellow", "FAIL": "bold_red",
             "info": "dim"}
    for level, line in findings:
        print(f"[{c(tones[level], level.center(4))}] {line}")
    fails = any(l == "FAIL" for l, _ in findings)
    warns = any(l == "WARN" for l, _ in findings)
    verdict = ("this environment is not usable yet" if fails else
               "usable, with things worth fixing" if warns else
               "this box is fully configured")
    print(c("bold_red" if fails else "yellow" if warns else "bold_green",
            f"doctor: {verdict}"))
    return EXIT_ERROR if fails else EXIT_DRIFT if warns else EXIT_CLEAN


_MERGE_HELP = dict(
    formatter_class=argparse.RawDescriptionHelpFormatter,
    description="Resolve files that differ on BOTH sides in your own diff tool, "
                "against their common ancestor. Nothing is installed until the "
                "result is checked for content that went missing, and until "
                "you pass --accept.",
    epilog="""the usual loop:
  ccs merge --dry-run                       # what would merge, and which ancestor each file would use
  ccs merge --preview                       # look at the three sides in your tool; decide nothing
  ccs merge                                 # resolve; the result waits in the workspace
  ccs merge --accept                        # install it on both sides, originals backed up

a machine whose file forked BEFORE the payload existed has no ancestor in the
checkout. Hand the merge a base, and look before you leap:
  ccs merge --only dotclaude/CLAUDE.md --base-from <repo>@<sha>:.claude/CLAUDE.md --dry-run
      # which ancestor; what it would let go of. `lost` must be 0
  ccs merge --only dotclaude/CLAUDE.md --base-from <repo>@<sha>:.claude/CLAUDE.md
      # every region the payload removed that this box kept is a hunk you decide
  ccs merge --only dotclaude/CLAUDE.md --base-from <repo>@<sha>:.claude/CLAUDE.md --accept
      # installs the LIVE file only; the checkout stays at HEAD

where that <repo>@<sha>:<path> comes from -- the ancestor is the last revision
BOTH copies descended from: the file as it was when this box was first set up
from another machine. It is almost always in that other machine's git history:
  git -C <repo> log --oneline --date=short --format="%h %ad %s" -- .claude/CLAUDE.md
      # <repo> = the home repo of the machine that seeded this box (many
      #          people track ~/.claude in one), a backup repo, or the payload
      #          itself if it is old enough; .claude/CLAUDE.md = the file's path
      #          inside that repo. Pick the commit from around the day the box
      #          was set up -- the most recent one BEFORE the two copies diverged.
  git -C <repo> show <sha>:.claude/CLAUDE.md | head     # eyeball it; it should read like both copies' common past
  (a copy of that old file on disk works too: --base-file FILE)

how to know you picked the right one -- the --dry-run table:
  `lost` must be 0 (if it is not, that is a tool bug, not your ancestor); and the
  first lines it names under "retired upstream" / "ours-del" should be sections
  you recognise as the OTHER machine's, or as genuinely retired. A heading THIS
  box wrote showing up as "retired upstream" means the ancestor never held it --
  wrong revision; try an earlier one.

the long version, with what a wrapped hunk looks like in your tool and what each
refusal means: docs/merge.md in the ccs repository.
""")


def _build_parser() -> argparse.ArgumentParser:
    # Every verb assumes a checkout already exists, so --help must answer
    # "where do I get one?" -- otherwise the cold start is a dead end.
    p = argparse.ArgumentParser(
        prog="ccs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Sync Claude Code configuration across machines "
                    "(dazzle-claude-config).",
        epilog="""first run (a payload is an ordinary git repo -- clone it yourself):
  git clone <payload-repo> ~/claude/my-config
  ccs status --checkout-dir ~/claude/my-config   # look before you leap
  ccs apply  --checkout-dir ~/claude/my-config   # install it; originals backed up

  Clone into ~/claude (or anywhere), NOT into ~/.claude -- the checkout is
  not your live config. Set CCS_CHECKOUT_DIR to stop passing --checkout-dir.

day to day, once it is installed:
  ccs status                     # what drifted, and which side owns each change
  ccs merge                      # files changed on BOTH sides -- see the warning below
  ccs collect                    # one-sided: your live edits -> the checkout
  ccs apply                      # one-sided: the checkout -> your live config
  ccs git add -A && ccs git commit && ccs git push   # share it, from any directory

  Directions are named from the payload's side: the checkout COLLECTS from a
  box; its contents APPLY to a box (the same `apply` as chezmoi and kubectl).

  A file that changed on BOTH sides needs `ccs merge`. `collect` and `apply`
  are ONE-WAY copies, so running either on such a file discards whatever the
  losing side added -- silently, and with a success message. `ccs status`
  marks those entries; `ccs merge --preview` shows you the three versions in
  your own diff tool before you commit to anything.

  A machine whose file forked BEFORE the payload existed has no ancestor in
  the checkout; `ccs merge -h` shows how to hand it one.

  Preferences (diff tool, status verbosity, AI merge command) live in
  ~/claude/ccs-config.json; each is also a per-run flag and an env var.

  A public collection to try: https://github.com/DazzleML/dazzle-claude-code-config
""")
    p.add_argument("--version", action="version",
                   version=f"ccs {_version.DISPLAY_VERSION}")
    _add_common(p)
    sub = p.add_subparsers(dest="verb", required=True)

    for verb, doc in (("collect", "live -> checkout: gather this box's changes "
                                  "into the payload (guarded)"),
                      ("apply", "checkout -> live: deliver the payload's config "
                                "to this box (backed up)"),
                      ("merge", "resolve files that differ on BOTH sides, in your diff tool"),
                      ("status", "three-way drift report (exit 1 when drift)"),
                      ("diff", "list per-file differences")):
        sp = sub.add_parser(verb, help=doc, **(_MERGE_HELP if verb == "merge" else {}))
        _add_common(sp, suppress=True)
        if verb in ("collect", "apply"):
            sp.add_argument("--dry-run", action="store_true")
        if verb == "merge":
            sp.add_argument("--dry-run", action="store_true",
                            help="list what would merge and, per file, which ancestor "
                                 "it would use and what that ancestor would let go of "
                                 "(the loss table; `lost` must be 0). Nothing is written")
        if verb in ("collect", "apply"):
            sp.add_argument("--force", action="store_true",
                            help="copy even files that changed on BOTH sides "
                                 "(DESTRUCTIVE -- discards the losing side; "
                                 "prefer ccs merge)")
            sp.add_argument("--require-current", action="store_true",
                            help="refuse (exit 1) when the checkout is behind its "
                                 "upstream, instead of warning and proceeding "
                                 "(config: require_current)")
        if verb == "collect":
            sp.add_argument("--only", default=None,
                            help="limit to one entry (dotclaude/skills), a parent of entries "
                                 "(dotclaude), or a subtree inside an entry "
                                 "(dotclaude/skills/test-mutation); whole path components only")
            sp.add_argument("--add", action="store_true",
                            help="also copy files the checkout does not have yet "
                                 "(default: update tracked files only, so a collect "
                                 "never publishes something you did not ask for)")
        if verb == "merge":
            sp.add_argument("--tool", default=None,
                            help="git mergetool name (default: probe for one whose "
                                 "binary actually exists)")
            sp.add_argument("--only", default=None,
                            help="limit to one entry, a parent of entries, or a subtree / file "
                                 "inside an entry (dotclaude/skills/x/SKILL.md); whole path "
                                 "components only")
            sp.add_argument("--accept", action="store_true",
                            help="install validated results into BOTH sides "
                                 "(default: leave them in the workspace for review)")
            sp.add_argument("--union", action="store_true",
                            help="resolve conflicting regions by KEEPING BOTH sides "
                                 "instead of emitting markers; right when each side "
                                 "added different things, wrong when they edited the "
                                 "same line (validation still runs)")
            sp.add_argument("--base", default="auto",
                            choices=("auto", "sibling", "none"),
                            help="auto: use a trusted ancestor, else none. "
                                 "sibling: use the nearest historical version even "
                                 "though it is NOT an ancestor (pre-resolves more, "
                                 "but invents deletions for content one side never "
                                 "had). none: force a 2-way.")
            sp.add_argument("--preview", action="store_true",
                            help="open the three sides in your diff tool to LOOK "
                                 "at them; validates nothing, installs nothing")
            sp.add_argument("--no-launch", action="store_true",
                            help="produce and validate the merged file without "
                                 "opening a diff tool")
        if verb in ("merge", "diff"):
            sp.add_argument("--base-file", default=None, metavar="FILE",
                            help="use FILE as the common ancestor instead of "
                                 "inferring one from the checkout's history -- for "
                                 "ADOPTING a box whose file forked before the payload "
                                 "existed. One file only: scope merge with --only")
            sp.add_argument("--base-from", default=None, metavar="REPO[@SHA]:PATH",
                            help="like --base-file, but read the ancestor out of "
                                 "another git repository (SHA defaults to HEAD)")
        if verb == "merge":
            sp.add_argument("--block-swap-ratio", default=None, type=float,
                            metavar="R",
                            help="with a supplied base: a region the payload "
                                 "REPLACED counts as a removal (and becomes a "
                                 "reviewer hunk) when fewer than half its lines have "
                                 "an R-similar line in the replacement. Higher R = "
                                 "more rewrites treated as removals = more hunks to "
                                 "review; lower R = fewer. Default 0.6; plateau "
                                 "0.45-0.70 on the real file")
        if verb == "status":
            g = sp.add_mutually_exclusive_group()
            g.add_argument("--long", action="store_true",
                           help="always list every differing file, ignoring the "
                                "line budget")
            g.add_argument("--compact", action="store_true",
                           help="one line per entry, never the per-file breakdown")
            gp = sp.add_mutually_exclusive_group()
            gp.add_argument("--pull", dest="pull", action="store_true", default=None,
                            help="when the fetch finds the checkout behind and "
                                 "fast-forwardable, fast-forward it first and report "
                                 "the real drift (config: auto_pull). Never merges, "
                                 "rebases, or stashes -- a divergent branch is "
                                 "reported, not resolved")
            gp.add_argument("--no-pull", dest="pull", action="store_false",
                            help="never pull, even with auto_pull set in the config")
        if verb == "diff":
            sp.add_argument("path", nargs="?", default=None,
                            help="show the actual line-by-line difference for one "
                                 "file (default: list which files differ)")
            sp.add_argument("--difftool", nargs="?", const=2, type=int, choices=(2, 3),
                            default=None, metavar="{2,3}",
                            help="open the file in your diff tool instead of printing: "
                                 "2 (default) = live vs checkout; 3 = live | inferred base | "
                                 "checkout, the three-way view, so the base ccs chose can be "
                                 "checked by eye (needs a path)")
            sp.add_argument("--tool", default=None,
                            help="git difftool name (default: probe for one whose "
                                 "binary actually exists)")
        if verb == "apply":
            sp.add_argument("--only", default=None,
                            help="limit to one entry (dotclaude/skills), a parent of entries "
                                 "(dotclaude), or a subtree inside an entry "
                                 "(dotclaude/skills/test-mutation); whole path components only")
            sp.add_argument("--sync-removals", action="store_true",
                            help="stage live-only files into the backup dir "
                                 "(default: report only)")
            sp.add_argument("--reseed", default=None, metavar="TARGET",
                            help="for ONE seed-if-absent entry (by target or repo "
                                 "path, e.g. CLAUDE.md): back the existing live file "
                                 "into this run's backup dir and write the payload's "
                                 "fresh seed over it -- the migration move for a box "
                                 "that predates the seed")
    # Registered for `ccs -h` and as a fallback; the real dispatch happens
    # BEFORE argparse in _split_git_passthrough (see the note there), so
    # git's own flags are never mistaken for ccs's.
    gp = sub.add_parser("git", add_help=False,
                        help="run git in the checkout, from anywhere -- everything "
                             "after `git` goes to git verbatim (ccs git pull, "
                             "ccs git push, ccs git log ...)")
    gp.add_argument("gitargs", nargs=argparse.REMAINDER)

    sd = sub.add_parser("seed", help="seeded files are yours after delivery; "
                        "record the answer to \"yours or the payload's?\" "
                        "(keep | reset | list; -h explains the words)",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        epilog="""what "seeded" means:
  A seed-if-absent entry delivers a STARTER FILE once, to a machine that
  lacks it. After that the file is yours: ccs never copies it again in
  either direction, and status does not count it as drift.

the words on a status/doctor line:
  the payload's (copy / starter / seed)
      the version in the checkout -- what a brand-new machine would get
  your copy / yours
      the live file it delivered, owned by this box ever since
  "differs"
      the two are no longer the same: you edited yours, the payload
      shipped a newer starter, or both

where the files live:
  your copy       under ~/.claude or ~/claude (the entry's target --
                  the exact path is printed under the status line)
  the payload's   inside the checkout (printed alongside)
  your decisions  ~/claude/ccs-seed-decisions.json (plain JSON, yours
                  to edit; ccs seed reset removes one entry cleanly)

the three answers when yours differs:
  keep yours      ccs seed keep <file> --until-changed   (asks again only
                  if the payload's starter changes; --always never asks)
  take theirs     ccs apply --reseed <file>   (your copy is backed up
                  first, to the apply run's backup folder)
  look first      open the two printed paths in any diff tool
""")
    _add_common(sd, suppress=True)
    sd.add_argument("action", choices=("keep", "reset", "list"))
    sd.add_argument("target", nargs="?", default=None,
                    help="the seed entry's target or repo path (e.g. CLAUDE.md)")
    sg = sd.add_mutually_exclusive_group()
    sg.add_argument("--always", action="store_true",
                    help="keep yours for good -- status stays quiet about it")
    sg.add_argument("--until-changed", action="store_true",
                    help="keep yours until the payload's seed changes, then "
                         "status asks again (the default)")

    st = sub.add_parser("setup", help="configure this machine -- bare `setup` "
                        "runs the doctor check (what is configured, what is "
                        "not, and the command that fixes each); `setup box` "
                        "declares its name and tags (never overwrites)",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        description="Configure this machine, or see how far "
                                    "its configuration has gotten.",
                        epilog="""the two forms:
  ccs setup        the check-up: every piece of the environment, each with
                   OK / WARN / FAIL and the command that fixes it (same as
                   `ccs doctor`). Run this first when anything confuses you.
  ccs setup box    declare this machine's identity, once

the words:
  checkout         your local clone of the payload repo -- the source of
                   everything ccs delivers
  box              one machine, under the name IT declares (never a
                   hostname) -- written to ~/claude/ccs-box.json
  tags             labels the box declares about itself (its own name is
                   always one). Manifest entries marked with tags are
                   delivered only to boxes that declare ALL of them --
                   how per-machine and per-role files stay per-machine
  seeded files     starter files delivered once and then owned by this
                   box -- `ccs seed -h` explains those words in full
""")
    _add_common(st, suppress=True)
    st.add_argument("what", choices=("box",), nargs="?", default=None,
                    help="`box` declares this machine's name and tags; leave "
                         "it off to run the check-up instead")
    st.add_argument("--name", default=None,
                    help="the box's declared name (lowercase; a chosen name, "
                         "not a hostname)")
    st.add_argument("--tag", action="append", default=None, metavar="TAG",
                    help="declare a tag (repeatable); the name itself is "
                         "always included")

    dr = sub.add_parser("doctor", help="read-only environment check: "
                        "interpreter, git, checkout, remote, box identity, "
                        "config, manifest, seeds. Changes nothing",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        description="Check every piece of this machine's ccs "
                                    "environment, read-only, and say what "
                                    "would fix each gap. Changes nothing. "
                                    "Exit 0 healthy / 1 warnings / 2 failures.",
                        epilog="the words the findings use (box, tags, "
                               "checkout, seeded) are explained in "
                               "`ccs setup -h`; seeded files in full in "
                               "`ccs seed -h`")
    _add_common(dr, suppress=True)
    return p


def _setup(args) -> tuple[Manifest, Path, dict, CheckoutRepo | None]:
    checkout = Path(args.checkout_dir).resolve() if args.checkout_dir \
        else default_checkout_dir()
    if not checkout.is_dir():
        raise ManifestError(
            f"checkout not found: {checkout} (clone the payload repo first, "
            "pass --checkout-dir, or set CCS_CHECKOUT_DIR)")
    try:
        manifest = Manifest.load(checkout)
    except ManifestError as e:
        if "manifest not found" not in str(e):
            raise
        # Layout-agnostic fallback: a bare mirror of a ~/.claude dir.
        manifest = Manifest.implicit(checkout)
        print(f"note: no ccs-manifest.json -- using implicit ~/.claude layout "
              f"({len(manifest.entries)} standard surfaces detected)")
    roots = territory_roots(args.claude_dir, args.user_claude)
    try:
        repo = CheckoutRepo(checkout)
    except GitError:
        repo = None  # plain directory checkout: allowed, A8/A11 checks skipped
    return manifest, checkout, roots, repo


def _classify(checkout, d, rel):
    """Which side owns the change in one differing file -- the same question
    the collect/apply guard answers, through the same `infer_base`, so status
    and the guard cannot disagree.

    Returns (kind, evidence). kind is one of:
      "one-sided"  -- an equal ancestor proves one side holds nothing unique
      "two-sided"  -- a base was found and both sides moved away from it
      "no base"    -- nothing in history attributes the change; treated as
                      two-sided (unknown is not safe), and says so
      "local snap" -- the path was never committed: the checkout copy is a
                      stale local snapshot, not the other machine's work
      "differs"    -- no git repo at all; nothing to attribute against
    A `status` label used to say "both" for every file that merely differed,
    with no base consulted (found 2026-08-21). This is the honest version.
    """
    if checkout is None:
        return "differs", "no git history to attribute against"
    repo_path = f"{d.entry.repo}/{rel}" if rel else d.entry.repo
    lv = d.live_base / rel if rel else d.live_base
    import subprocess
    shown = subprocess.run(["git", "show", f"HEAD:{repo_path}"],
                           cwd=str(checkout), capture_output=True)
    if shown.returncode != 0 or not shown.stdout:
        return "local snap", "never committed -- the checkout copy is a local snapshot; collect replaces it"
    ours = lv.read_bytes()
    theirs = shown.stdout
    found = merge.infer_base(checkout, repo_path, ours, theirs)
    if found is None:
        return "no base", "no ancestor in history attributes this change -- treat as both sides"
    base_n = _normalize_eol(found[0])
    if base_n == _normalize_eol(ours):
        return "one-sided", f"checkout ahead; live == {found[1]}"
    if base_n == _normalize_eol(theirs):
        return "one-sided", f"live ahead; checkout == {found[1]}"
    return "two-sided", f"both moved since {found[1]}"


_KIND_COLOR = {"one-sided": "green", "two-sided": "magenta", "no base": "yellow",
               "local snap": "yellow", "differs": "dim"}


def _print_entry_files(d, kinds=None) -> None:
    """Per-file breakdown, indented under its entry.

    An entry is usually a DIRECTORY (skills/, commands/, ...), so "2 files
    differ" leaves you asking *which two*. Indentation ties each file to its
    parent entry without repeating the entry path on every line.
    """
    # A single-file entry (CLAUDE.md) already said everything on its own line;
    # repeating it as a child is pure noise.
    names = [r for r in (*d.live_only, *d.repo_only, *d.modified) if r]
    if not names:
        return
    width = min(max(len(n) for n in names), 52)

    for rel in sorted(d.live_only):
        if rel:
            print(f"      {c('yellow', 'live only')}  {rel}")
    for rel in sorted(d.repo_only):
        if rel:
            print(f"      {c('cyan', 'checkout ')}  {rel}")
    for rel in sorted(d.modified):
        if not rel:
            continue
        ol, ch, orp, reg = line_stats(d.live_base / rel, d.repo_base / rel)
        stats = (f"{ol} only live / {ch} replaced / {orp} only checkout"
                 f", {reg} region{'' if reg == 1 else 's'}")
        kind, evidence = (kinds or {}).get(rel, ("differs", ""))
        label = c(_KIND_COLOR[kind], f"{kind:<10}")
        tail = f"{c('dim', stats)}" + (f"  {c('dim', '-- ' + evidence)}" if evidence else "")
        print(f"      {label}  {rel:<{width}}  {tail}")


class AmbiguousPath(Exception):
    """A user path matched more than one file; `.candidates` lists the
    qualified repo-side labels that would each have resolved."""

    def __init__(self, want: str, candidates: list[str]):
        super().__init__(want)
        self.want, self.candidates = want, candidates


def _suffix_match(label: str, want: str) -> bool:
    # Whole path components only: `SAME.md` matches `s/SAME.md`, never
    # `s/notSAME.md`.
    return label == want or label.endswith("/" + want)


def _print_ambiguous(e: AmbiguousPath) -> None:
    print(c("red", f"ambiguous: {e.want!r} matches {len(e.candidates)} files")
          + c("dim", " -- name one of them:"), file=sys.stderr)
    for label in e.candidates:
        print(f"  {label}", file=sys.stderr)


def _resolve_pair(all_diffs, want: str):
    """Find (live_path, repo_path, target_label, repo_label) for a user path.

    Looks in the DIFFERING set first, then falls back to resolving against the
    manifest, so a file that is perfectly in sync still resolves. Without the
    fallback an in-sync file was indistinguishable from a typo -- and after a
    merge, in-sync is the normal state.

    A bare filename that lives in two entries (`SAME.md` under both `s/` and
    `t/`) used to resolve to whichever entry came first in the manifest, with
    no sign that a second candidate existed (found by the v0.4.1 checklist
    sweep). That is a confident wrong answer at the path layer; it now raises
    AmbiguousPath so the caller can list the candidates and ask for a
    qualified path instead.
    """
    hits: list[tuple] = []
    for d in all_diffs:
        for rel in [*d.modified, *d.live_only, *d.repo_only]:
            target = f"{d.entry.target}/{rel}" if rel else d.entry.target
            repo = f"{d.entry.repo}/{rel}" if rel else d.entry.repo
            if _suffix_match(target, want) or _suffix_match(repo, want):
                lv = d.live_base / rel if rel else d.live_base
                rp = d.repo_base / rel if rel else d.repo_base
                hits.append((lv, rp, target, repo))
    if len(hits) > 1:
        raise AmbiguousPath(want, [h[3] for h in hits])
    if hits:
        return hits[0]
    for d in all_diffs:
        for label in (d.entry.target, d.entry.repo):
            if not label:
                continue
            rel = ""
            if _suffix_match(label, want):
                pass
            elif want.startswith(label.rstrip("/") + "/"):
                rel = want[len(label.rstrip("/")) + 1:]
            else:
                continue
            lv = d.live_base / rel if rel else d.live_base
            rp = d.repo_base / rel if rel else d.repo_base
            if lv.is_file() or rp.is_file():
                target = f"{d.entry.target}/{rel}" if rel else d.entry.target
                repo = f"{d.entry.repo}/{rel}" if rel else d.entry.repo
                return lv, rp, target, repo
    return None


def _launch_file_difftool(all_diffs, wanted: str, tool: str | None, *, supplied=None,
                          ways: int = 2, checkout=None, roots=None, repo=None) -> int:
    """Open one file in the user's own diff tool.

    ways=2: live vs checkout, through git's difftool registry.
    ways=3: live | inferred base | checkout, through git's MERGETOOL registry
            (the $LOCAL $BASE $REMOTE $MERGED contract), with the output pane
            pointed at a scratch copy that is never read back. This is how the
            base ccs chose for a file -- the thing every "one-sided"/"two-sided"
            verdict rests on -- gets checked by a human. `merge --preview` only
            opens files that differ on BOTH sides; one-sided files, where the
            attribution matters just as much, had no three-pane view at all.

    Launches whenever both sides RESOLVE, not only when they differ. If the
    user asked for the tool, open the tool -- confirming two files are
    identical by looking at them is a legitimate reason to want it, and
    refusing with "nothing to compare" answers a question nobody asked.
    """
    want = wanted.replace(chr(92), "/").strip("/")
    try:
        found = _resolve_pair(all_diffs, want)
    except AmbiguousPath as e:
        _print_ambiguous(e)
        return EXIT_ERROR
    if found is None:
        print(c("yellow", f"no file matches {wanted!r}")
              + c("dim", " -- run `ccs diff` with no argument to list what differs"))
        return EXIT_CLEAN
    lv, rp, target, repo_label = found
    if ways == 3:
        return _launch_three_way(lv, rp, target, repo_label, tool, checkout, roots, repo,
                                 supplied=supplied)
    repo = repo_label
    # A file present on only one side is an ADD or a REMOVE, and looking at
    # it is exactly what you want -- refusing to open it answers a question
    # nobody asked. Substitute an empty file for the absent side, which is
    # what git difftool does for a new file.
    import tempfile
    empty = None
    if not lv.is_file() or not rp.is_file():
        empty = pathlib.Path(tempfile.mkdtemp()) / "(absent)"
        empty.write_bytes(b"")
        if not lv.is_file():
            lv = empty
        if not rp.is_file():
            rp = empty
    name = merge.resolve_difftool(tool)
    merge.launch_difftool(name, rp, lv)
    same = empty is None and not files_differ(lv, rp)
    note = " (identical -- opened anyway, you asked)" if same else ""
    print(c("dim", f"opened {name}: checkout/{repo} vs live/{target}{note}"))
    return EXIT_CLEAN if same else EXIT_DRIFT


def _launch_three_way(lv, rp, target, repo_label, tool, checkout, roots, repo,
                      supplied=None) -> int:
    """live | base | checkout in the user's mergetool, read-only.

    The base is whatever `infer_base` picks -- the same call `status` and the
    collect/apply guard make -- so what opens is the tool's actual reasoning,
    not a reconstruction of it. If no base can be inferred (no git repo, no
    history, or every candidate rejected) it says so and opens the two-way
    instead: an honest two-pane beats a three-pane with an invented center.
    """
    import shutil
    import subprocess
    from .manifest import Entry
    if checkout is None or repo is None or not lv.is_file() or not rp.is_file():
        why = ("the checkout is not a git repository" if repo is None
               else "one side is absent, so there is no history to attribute against")
        print(c("yellow", f"no base possible -- {why}; opening the two-way view"))
        return _two_way_fallback(lv, rp, target, repo_label, tool)
    shown = subprocess.run(["git", "show", f"HEAD:{repo_label}"], cwd=str(checkout),
                           capture_output=True)
    theirs = shown.stdout if shown.returncode == 0 else b""
    if supplied is not None and supplied[0] is not None:
        found = (supplied[0], "supplied")          # a fact, not an estimate
    else:
        found = merge.infer_base(checkout, repo_label, lv.read_bytes(), theirs) if theirs else None
    if found is None:
        why = ("the path was never committed" if not theirs
               else "no ancestor in history attributes this change")
        print(c("yellow", f"no base could be inferred -- {why}; opening the two-way view"))
        return _two_way_fallback(lv, rp, target, repo_label, tool)
    base_bytes, sha = found
    ws = merge.workspace_for(roots) / "look"
    ws.mkdir(parents=True, exist_ok=True)
    stem = repo_label.replace("/", "__").replace(chr(92), "__")
    base_f = ws / f"{stem}.base-{sha}"
    base_f.write_bytes(base_bytes)
    merged_f = ws / f"{stem}.look-only"          # the output pane; never read back
    shutil.copyfile(lv, merged_f)
    item = merge.MergeItem(entry=Entry(repo=repo_label, strategy="copy"),
                           rel="", live=lv, repo=rp, base=base_f, repo_dest=None)
    name = merge.resolve_tool(tool)
    base_n = merge._normalize_eol(base_bytes)
    side = ("== live (checkout ahead)" if base_n == merge._normalize_eol(lv.read_bytes())
            else "== checkout (live ahead)" if base_n == merge._normalize_eol(theirs)
            else "neither side (both moved)")
    merge.launch(name, item, merged_f, base_f, wait=False)
    print(c("dim", f"opened {name} (3-way): live | base {sha} {side} | checkout/{repo_label}"))
    print(c("dim", f"  output pane is a scratch copy ({merged_f.name}); nothing is written back"))
    return EXIT_DRIFT


def _two_way_fallback(lv, rp, target, repo_label, tool) -> int:
    name = merge.resolve_difftool(tool)
    merge.launch_difftool(name, rp, lv)
    print(c("dim", f"opened {name}: checkout/{repo_label} vs live/{target}"))
    return EXIT_DRIFT


def _print_file_diff(all_diffs, wanted: str, manifest=None, box=None) -> int:
    """`ccs diff <path>`: print the line-by-line difference for one file.

    The printed counterpart of `--difftool`, sharing its path resolution so the
    two cannot drift. Three outcomes, each distinct on purpose (they were once
    conflated as "no match"):
      differs   -> unified diff, exit 1
      identical -> one line saying so, exit 0 (the normal case right after a
                   merge, and the claim "merged and installed" is checked by it)
      no match  -> exit 2
    Line endings are normalised before comparing, as everywhere else, so a
    CRLF-vs-LF file reads as identical rather than as a wall of changes.
    """
    import difflib
    want = wanted.replace(chr(92), "/").strip("/")
    try:
        found = _resolve_pair(all_diffs, want)
    except AmbiguousPath as e:
        _print_ambiguous(e)
        return EXIT_ERROR
    if found is None:
        hidden = _gated_matches(manifest, box, lambda r: _suffix_match(r, want)
                                or want.startswith(r.rstrip("/") + "/"))             if manifest is not None else []
        if hidden:
            print(c("red", f"not for this box: {wanted!r}")
                  + c("dim", " -- its entry is gated off here: " + "; ".join(hidden)),
                  file=sys.stderr)
        else:
            print(c("red", f"no such file in any manifest entry: {wanted!r}")
                  + c("dim", " -- run `ccs diff` with no argument to list what differs"),
                  file=sys.stderr)
        return EXIT_ERROR
    lv, rp, target, repo = found
    left = _normalize_eol(rp.read_bytes()) if rp.is_file() else b""
    right = _normalize_eol(lv.read_bytes()) if lv.is_file() else b""
    if left == right:
        print(c("green", "identical") + f" -- live and the checkout agree: live/{target}")
        return EXIT_CLEAN
    a = left.decode("utf-8", "replace").splitlines(keepends=True)
    b = right.decode("utf-8", "replace").splitlines(keepends=True)
    absent = (" (absent in checkout)" if not rp.is_file() else
              " (absent in live)" if not lv.is_file() else "")
    print(c("bold", f"checkout/{repo}  ->  live/{target}{absent}"))
    for line in difflib.unified_diff(a, b, fromfile=f"checkout/{repo}",
                                     tofile=f"live/{target}", n=3):
        s = line.rstrip("\r\n")
        if line.startswith("+") and not line.startswith("+++"):
            print(c("green", s))
        elif line.startswith("-") and not line.startswith("---"):
            print(c("red", s))
        elif line.startswith("@@"):
            print(c("cyan", s))
        else:
            print(s)
    return EXIT_DRIFT


def _remote_state(repo, cfg, args):
    """Fetch (unless disabled) and report (fetched, detail, behind).

    fetched: True  -- a fetch succeeded this run; the branch line is current
             None  -- skipped (--no-fetch / fetch: false / no upstream)
             False -- failed; `detail` carries git's first stderr line
    behind:  commits the upstream has that HEAD lacks, per the tracking ref
             as of now (after the fetch, if one ran); None without an upstream.

    One fetch per process. Every verb that wants to know whether a `git pull`
    is due goes through here, so `status` and the verbs cannot disagree about
    what "behind" means -- the same rule as sharing infer_base for attribution.
    """
    if repo is None:
        return None, "", None
    want = bool((cfg or {}).get("fetch", True)) and not getattr(args, "no_fetch", False)
    fetched, detail = (None, "")
    if want:
        fetched, detail = repo.fetch(timeout=int((cfg or {}).get("fetch_timeout", 15)))
    _ahead, behind = repo.ahead_behind()
    return fetched, detail, behind


def _supplied_base(args) -> tuple[bytes | None, str]:
    """(blob, label) for --base-file / --base-from, or (None, '')."""
    from . import basefind
    bf = getattr(args, "base_file", None)
    bfrom = getattr(args, "base_from", None)
    if bf and bfrom:
        raise merge.MergeError("--base-file and --base-from are alternatives; pass one")
    if bf:
        path = Path(bf).expanduser()
        if not path.is_file():
            raise merge.MergeError(f"--base-file: not a file: {bf}")
        return path.read_bytes(), f"file:{path.name}"
    if bfrom:
        try:
            return basefind.read_base_from(bfrom)
        except ValueError as e:
            raise merge.MergeError(str(e)) from e
    return None, ""


def _loss_row(tag, origin, label, d_ours, d_theirs, phantom, table, verdict) -> str:
    o, t = table.ours, table.theirs
    return (f" {tag:<2} {origin:<9} {label:<28} {d_ours:>7} {d_theirs:>9}  {phantom:<7} "
            f"{table.hunks:>5} | {o.silent:>6} {o.honoured:>7} {o.lost:>4} | "
            f"{t.silent:>6} {t.honoured:>8} {t.lost:>4} | {verdict}")


_LOSS_HEAD = (" #  origin    base                          d(ours) d(theirs)  phantom  "
              "hunks | ours: silent retired lost | theirs: silent ours-del lost | verdict")


def _print_base_table(item, checkout, roots, supplied: tuple, ratio) -> None:
    """`merge --dry-run`: per file, the table that is the oracle for a merge.

    One row per candidate base -- the supplied one (phantom-exempt) and the
    inferred one (with the phantom verdict the guard would give it, or the
    rule-4 rejection) -- each with the hunk count the merge would produce and
    the per-side loss numbers. `lost` is the one that must be 0: it counts a
    side's own additions missing from both the clean output and every hunk,
    which no base can legitimately cause. Same code `merge` seeds from, so
    the table and the merge cannot disagree.
    """
    from . import basefind
    repo_label = f"{item.entry.repo}/{item.rel}" if item.rel else item.entry.repo
    ours_b = item.live.read_bytes() if item.live.is_file() else b""
    theirs_b = item.repo.read_bytes() if item.repo.is_file() else b""
    if not ours_b or not theirs_b:
        print(c("dim", "    (one side is absent; nothing to attribute)"))
        return
    ours_l, theirs_l = basefind.lines_of(ours_b), basefind.lines_of(theirs_b)
    ws = merge.workspace_for(roots) / "plan"
    ws.mkdir(parents=True, exist_ok=True)
    ratio = ratio if ratio is not None else basefind.DEFAULT_RATIO
    print(c("dim", _LOSS_HEAD))
    rows = 0
    usable = None
    blob, label = supplied
    if blob is not None:
        base_l = basefind.lines_of(blob)
        out, stats = basefind.conflict_on_delete(ours_l, base_l, theirs_l, ws / "supplied", ratio)
        table = basefind.loss_table(ours_l, theirs_l, base_l, out)
        verdict = "USABLE  conflict-on-delete on" if table.lost == 0 else "TOOL BUG  lost != 0"
        rows += 1
        print(_loss_row(rows, "supplied", label[:28], basefind.distance(base_l, ours_l),
                        basefind.distance(base_l, theirs_l), "exempt", table, verdict))
        usable = usable or (table.lost == 0 and (label, table, stats))
    rej: list = []
    inferred = merge.infer_base(checkout, repo_label, ours_b, theirs_b, rejected=rej)
    rows += 1
    if inferred is not None:
        base_b, sha = inferred
        base_l = basefind.lines_of(base_b)
        pr, pn = merge.base_phantom_ratio(base_b, ours_b, theirs_b)
        out, rc = basefind.merge_file_diff3(ours_l, base_l, theirs_l, ws / "inferred")
        table = basefind.loss_table(ours_l, theirs_l, base_l, out)
        verdict = "USABLE  (history)" if table.lost == 0 else "TOOL BUG  lost != 0"
        print(_loss_row(rows, "inferred", f"{sha}  checkout history", basefind.distance(base_l, ours_l),
                        basefind.distance(base_l, theirs_l), f"{pr:.2f}/{pn}", table, verdict))
        usable = usable or (table.lost == 0 and (sha, table, None))
    elif rej:
        _, sha, n, pr = rej[0]
        print(f" {rows:<2} inferred  (none)  nearest {sha} rejected   {'--':>7} {'--':>9}  "
              f"{pr:.2f}/{n:<4} {'--':>5} | {'--':>6} {'--':>7} {'--':>4} | {'--':>6} {'--':>8} {'--':>4} "
              f"| NO BASE  rule 4: nearest ({sha}) is a sibling, not an ancestor")
    else:
        print(f" {rows:<2} inferred  (none)  checkout history           {'--':>7} {'--':>9}  "
              f"{'--':<7} {'--':>5} | {'--':>6} {'--':>7} {'--':>4} | {'--':>6} {'--':>8} {'--':>4} "
              f"| NO BASE  nothing in history attributes this change")
    if usable:
        label, table, stats = usable
        o, t = table.ours, table.theirs
        line = (f"  base: use {label} -- {table.hunks} hunk(s) to review; {table.lost} line(s) lost; "
                f"{o.honoured} line(s) of yours retired upstream (theirs wins)")
        if o.first_honoured:
            line += f": first {o.first_honoured[:50]!r}"
        print(c("green", line))
        if t.honoured:
            print(c("dim", f"        {t.honoured} theirs line(s) stay deleted (you removed them since base)"
                           + (f": first {t.first_honoured[:50]!r}" if t.first_honoured else "")))
        if stats is not None:
            print(c("dim", f"        conflict-on-delete: {stats.regions} removed region(s), "
                           f"{stats.region_lines} base line(s); {stats.natural} natural + "
                           f"{stats.wrapped} wrapped hunk(s) ({stats.in_hunk} inside natural hunks)"))
    else:
        print(c("yellow", "  base: none usable -- supply one with --base-file / --base-from, "
                          "or merge two-way (no base)"))


def _print_honoured(v) -> None:
    """The deletions a three-way merge honoured, per side, with the first
    line of each region. This is informational on a right base and the
    TRIPWIRE on a wrong one: a box that sees its own section heading under
    "retired upstream" has merged against a base that never held it."""
    labels = {"ours": "retired upstream (theirs deleted since base)",
              "theirs": "retired here (you deleted since base)"}
    for side, regions in v.honoured.items():
        n = sum(len(r) for r in regions)
        print(c("dim", f"    {labels[side]}: {n} line(s) in "
                       f"{len(regions)} region(s)"))
        for reg in regions[:6]:
            print(c("dim", f"      - {reg[0].strip()[:90]}"))
        if len(regions) > 6:
            print(c("dim", f"      ... and {len(regions) - 6} more region(s)"))


def _gated_matches(manifest, box, pred) -> list[str]:
    """Entries the tag/os gate kept off this box that `pred(repo)` would have
    reached -- so a miss can say "not for this box" instead of "no such
    entry". The gate runs upstream of every verb; without this, a typo and
    a missing tag print the same words."""
    tags = box.tags if box is not None else frozenset()
    out = []
    for e in manifest.entries:
        if e.strategy == "plugins" or not pred(e.repo):
            continue
        why = entry_gate_reason(e, tags)
        if why:
            out.append(f"{e.repo} (needs {why})")
    return out


def _warn_only_miss(args, manifest, box) -> None:
    hidden = _gated_matches(manifest, box, lambda r: only_scope(args.only, r)[0])
    if hidden:
        print(c("yellow", f"warning: --only {args.only!r} matches only entries "
                          f"this box is not tagged for: " + "; ".join(hidden)))
    else:
        print(c("yellow", f"warning: --only {args.only!r} matched no manifest entries"))


def _norm_sha(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _seed_findings(manifest, checkout, roots, repo, box_tags, user_claude):
    """Per FILE seed entry, what `status` should say about it (issue #27).

    States: absent (will seed) | matches | untouched-old (the payload
    replaced a seed this box never edited -- auto-offer --reseed, no
    question) | open (customized, no decision recorded) | kept-always |
    kept-current (until-changed, seed unchanged) | reopened (until-changed,
    the seed moved since the decision).

    EOL-insensitive throughout: history stores LF, live Windows files are
    CRLF; a raw comparison never matches anything (measured -- see
    tests/one-offs/poc_seed_ancestry_probe.py).
    """
    from . import seeddecisions
    from .syncmap import entry_applies, entry_bases
    dec = seeddecisions.load(user_claude)
    findings: list[tuple[str, str, str]] = []
    # A seed entry can be the FALLBACK for a target a tag-gated copy entry
    # also delivers (machine.template.md seeds boxes that machines/<name>/
    # does not cover). Where the copy entry applies, the copy governs --
    # asking "yours or the payload's?" about that file here would be wrong.
    covered = {(e.territory, e.target) for e in manifest.entries
               if e.strategy == "copy" and entry_applies(e, box_tags)}
    for entry in manifest.seed_entries():
        if not entry_applies(entry, box_tags):
            continue
        if (entry.territory, entry.target) in covered:
            continue
        live_base, repo_base = entry_bases(
            entry, checkout, roots, manifest.territories)
        if not repo_base.is_file():
            continue    # directory seeds: per-file reporting is #28 follow-up
        if not live_base.is_file():
            findings.append((entry.target, "absent", live_base, repo_base))
            continue
        try:
            live, seed = live_base.read_bytes(), repo_base.read_bytes()
        except OSError:
            continue
        current_sha = _norm_sha(seed)
        live_sha = _norm_sha(live)
        if live_sha == current_sha:
            findings.append((entry.target, "matches", live_base, repo_base))
            continue
        hist = repo.seed_history(entry.repo) if repo is not None else []
        if live_sha in {sha for _c, sha in hist} - {current_sha}:
            findings.append((entry.target, "untouched-old", live_base, repo_base))
            continue
        rec = dec.by_target.get(entry.target)
        state = ("open" if rec is None else
                 "kept-always" if rec.get("mode") == "always" else
                 "kept-current" if rec.get("seed_blob") == current_sha else
                 "reopened")
        findings.append((entry.target, state, live_base, repo_base))
    return findings, dec.errors


# Plain words, no project jargon: these lines are read by people who have
# never heard "seed" used this way. Every actionable line says what the
# file IS, what happened, and all three answers -- and the block prints
# both file paths, because advice about files nobody can find is noise
# (user finding, 2026-08-25).
_SEED_ACTIONABLE = {
    "untouched-old": ("yellow", "an unchanged copy of an older starter (the "
                                "payload has a newer one). ccs apply --reseed "
                                "{t} takes it; your copy is backed up first"),
    "open": ("yellow", "delivered once as a starter, then yours (and yours "
                       "now differs from the payload's version). Yours or the "
                       "payload's? keep yours: ccs seed keep {t}; take the "
                       "payload's: ccs apply --reseed {t} (backs yours up); "
                       "or open both files below in your diff tool first"),
    "reopened": ("yellow", "the payload's starter changed since you chose to "
                           "keep yours. Keep it again: ccs seed keep {t}; "
                           "take the payload's: ccs apply --reseed {t}; or "
                           "compare the files below"),
}
_SEED_QUIET = {
    "matches": "same as the payload's copy",
    "kept-always": "yours (you chose to keep it, always)",
    "kept-current": "yours (kept until the payload's copy changes)",
    "absent": "will be delivered on the next ccs apply",
}


def _seed_paths_line(live, repo_p, indent: str = "            ") -> str:
    return (f"{indent}" + c("dim", f"yours: {live}") + "\n"
            f"{indent}" + c("dim", f"the payload's: {repo_p}"))


def _print_seed_block(findings, errors, long_form: bool) -> None:
    """The `seeded` status block: actionable states always; the quiet
    inventory only in the long form (explanation, not drift -- exit codes
    are untouched, the ownership contract stands)."""
    for e in errors:
        print(c("yellow", f"warning: {e}"))
    actionable = [f for f in findings if f[1] in _SEED_ACTIONABLE]
    quiet = [f for f in findings if f[1] in _SEED_QUIET]
    if not actionable and not (long_form and quiet):
        return
    print(f"{c('bold', 'seeded')}    " +
          c("dim", "(starter files, delivered once and then yours -- ccs "
                   "never overwrites these; not counted as drift. "
                   "ccs seed -h explains)"))
    for t, s, live, repo_p in actionable:
        tone, msg = _SEED_ACTIONABLE[s]
        print(f"          {c(tone, t)} {c(tone, '-- ' + msg.format(t=t))}")
        print(_seed_paths_line(live, repo_p))
    if long_form:
        for t, s, _live, _repo in quiet:
            print(f"          {c('dim', t)} {c('dim', '-- ' + _SEED_QUIET[s])}")


def _print_status(checkout, repo, roots, all_diffs, diffs, cfg=None,
                  remote=(render.UNSPECIFIED, "", None),
                  manifest=None, box=None, pulled=None) -> None:
    """The status report, written for someone who has not read the docs.

    Three legs, because that is what "in sync" actually means here -- and
    each is a labelled PLACE (#22): `remote` (the hub every other machine
    syncs through), `checkout` (the folder), `live` (the territories). The
    remote's pull state used to ride as a clause under `checkout`, burying
    the one question a fleet user asks first: is there anything on the
    server my machines have not seen?
    """
    files = sum(d.total for d in all_diffs)
    # Attribute every differing file ONCE, through the guard's own infer_base.
    # Cost: one `git show` for HEAD plus the history walk per differing file;
    # an equal ancestor short-circuits the walk, so the common one-sided case
    # is two spawns. Without this, "both" meant "differs" (2026-08-21).
    classified: dict[int, dict[str, tuple[str, str]]] = {}
    co_path = checkout if repo is not None else None
    for d in diffs:
        if d.mismatch or not d.modified:
            continue
        classified[id(d)] = {rel: _classify(co_path, d, rel) for rel in d.modified}
    branch_raw = repo.branch_info() if repo is not None else ""
    if repo is not None:
        fetched, detail = remote[0], remote[1]
        host = render.remote_host(repo.remote_url())
        state = render.humanize_remote(branch_raw, fetched, detail, pulled)
        tone = ("yellow" if fetched is False or "behind" in state
                or "diverged" in state else "dim")
        if pulled is not None and pulled[1]:
            tone = "green"
        if host:
            print(f"{c('bold', 'remote')}    {c('cyan', host)}{c('dim', ':')} "
                  f"{c(tone, state)}")
        else:
            print(f"{c('bold', 'remote')}    {c(tone, state)} "
                  f"{c('dim', '(no remote configured)')}")
    name = render.branch_name(branch_raw)
    suffix = f"  {c('dim', f'(on {name})')}" if name else ""
    print(f"{c('bold', 'checkout')}  {c('cyan', str(checkout))}{suffix}")
    if repo is not None:
        dirty = len([l for l in repo.porcelain() if l.strip()])
        if dirty:
            # Build the plural separately: reusing the outer f-string's quote
            # character inside a nested f-string is a SyntaxError before 3.12
            # (PEP 701), and this package supports 3.10+.
            s = "" if dirty == 1 else "s"
            print(f"          {c('yellow', f'{dirty} uncommitted change{s} in the checkout')}"
                  f" {c('dim', '-- commit and push to share with your other machines')}")
        if repo.has_conflicts():
            print(c("bold_red", "          MERGE CONFLICTS -- resolve them before `ccs apply`"))
    # `checkout` gets a labelled path above; `live` needs the same so the two
    # words in every difference line below are unambiguous.
    print(f"{c('bold', 'live')}      {c('cyan', str(roots['CLAUDE_DIR']))} "
          f"{c('dim', '(dotclaude)')}")
    print(f"          {c('cyan', str(roots['USER_CLAUDE']))} "
          f"{c('dim', '(userclaude)')}")
    print(f"{c('bold', 'compared')}  {render.n_files(files)} across "
          f"{render.n_entries(len(all_diffs))} of live config vs the checkout")
    # Entries the gate kept off this box, with the reason, so "why is my
    # file not syncing" is answered on screen rather than by reading the
    # manifest. Shown only in the long form -- it is explanation, not drift.
    if manifest is not None and (cfg or {}).get('status_detail') == 'long':
        tags = box.tags if box is not None else frozenset()
        gated = [(e.repo, entry_gate_reason(e, tags))
                 for e in manifest.entries if e.strategy != 'plugins']
        gated = [(r, why) for r, why in gated if why]
        if gated:
            label = f"box {box.name}" if box is not None and box.name else "this box"
            declared = ', '.join(sorted(tags)) if tags else 'none'
            print(f"{c('bold', 'not for')}   {label} {c('dim', f'(tags declared: {declared})')}")
            for r, why in gated:
                print(f"          {c('dim', r)} {c('dim', '-- needs ' + why)}")

    cfg = cfg or {}
    detail = cfg.get("status_detail", "auto")
    budget = cfg.get("status_max_lines", 30)
    seed_findings, seed_errors = ([], [])
    if manifest is not None:
        seed_findings, seed_errors = _seed_findings(
            manifest, checkout, roots, repo,
            box.tags if box is not None else frozenset(),
            roots.get("USER_CLAUDE"))
        _print_seed_block(seed_findings, seed_errors,
                          long_form=(detail == "long"))
    # One line per entry, plus one per file it contains.
    would_cost = sum(1 + len(d.live_only) + len(d.repo_only) + len(d.modified)
                     for d in diffs)
    long_form = detail == "long" or (detail == "auto" and would_cost <= budget)

    if diffs:
        print()
        print(c("bold_yellow", f"differences in {render.n_entries(len(diffs))}:"))
        for d in diffs:
            if d.mismatch:
                print(f"  {c('red', d.entry.repo)}: {d.mismatch}")
                continue
            bits = []
            if d.live_only:
                bits.append(f"{len(d.live_only)} only in your live config")
            if d.repo_only:
                bits.append(f"{len(d.repo_only)} only in the checkout")
            if d.modified:
                n = len(d.modified)
                # Most entries are DIRECTORIES (skills/, commands/, ...), so the
                # file count matters there; for a single-file entry like
                # CLAUDE.md it is noise that reads as a difference count.
                single = n == 1 and d.repo_base.is_file()
                kinds = classified.get(id(d), {})
                k2 = sum(1 for k, _ in kinds.values() if k in ("two-sided", "no base"))
                if single:
                    # A single-file entry gets no per-file breakdown line, so its
                    # evidence (which commit a side equals) has to ride here.
                    kind, evidence = next(iter(kinds.values()), ("differs", ""))
                    label = (c('magenta', 'both sides') if kind in ("two-sided", "no base")
                             else c('green', 'one-sided') if kind == "one-sided" else kind)
                    bits.append("differs -- " + label
                                + (f" ({c('dim', evidence)})" if evidence else ""))
                else:
                    bits.append(f"{render.n_files(n)} {'differs' if n == 1 else 'differ'}"
                                + (f" ({c('magenta', str(k2))} on both sides)" if k2 else
                                   f" ({c('green', 'all one-sided')})" if kinds else ""))
                if n <= 25:
                    ol = ch = orp = reg = 0
                    for rel in d.modified:
                        lv = d.live_base / rel if rel else d.live_base
                        rp = d.repo_base / rel if rel else d.repo_base
                        a, b_, c_, r = line_stats(lv, rp)
                        ol += a; ch += b_; orp += c_; reg += r
                    detail = (f"{c('yellow', str(ol))} lines only in live, "
                              f"{c('magenta', str(ch))} replaced, "
                              f"{c('cyan', str(orp))} lines only in the checkout"
                              f", in {reg} region{'' if reg == 1 else 's'}")
                    bits[-1] += f" -- {detail}"
            print(f"  {c('cyan', d.entry.repo)}: {', '.join(bits)}")
            if long_form:
                _print_entry_files(d, classified.get(id(d)))

    # Only explain the collapse when the BUDGET caused it. If the user asked
    # for --compact, telling them they exceeded a budget is both wrong and
    # faintly accusatory.
    if diffs and not long_form and detail == "auto":
        print(c("dim", f"  ({would_cost} lines of per-file detail suppressed -- "
                       f"over the {budget}-line budget; use --long, or raise "
                       "status_max_lines in ~/claude/ccs-config.json)"))

    denied = [(d.entry.target, rel) for d in all_diffs for rel in d.denied_live]
    if denied:
        print()
        print(c("bold", "protected") + " " +
              c("dim", f"({render.n_files(len(denied))} kept out of sync on purpose -- "
                       "matches a deny rule, so ccs will not copy it in either direction)"))
        for target, rel in denied:
            print(f"  {c('magenta', f'{target}/{rel}')}")

    print()
    behind = remote[2] if len(remote) > 2 else None
    if not diffs and behind:
        up = repo.upstream() if repo is not None else "upstream"
        print(c("bold_yellow", "status: live matches the checkout") +
              f" -- but the checkout is {behind} behind {up}; "
              + c("bold", "git pull") + " then run status again")
    elif not diffs:
        # "Everything ccs SYNCS matches" is the honest claim (issue #27):
        # seeded files are the box's own and are not compared, and saying
        # "your live config and the checkout match" while a seeded file
        # differs by a thousand lines was measured to mislead exactly the
        # person mid-migration.
        n_own = sum(1 for _t, s, *_ in seed_findings
                    if s not in ("matches", "absent"))
        clause = ""
        if n_own:
            files = "file is" if n_own == 1 else "files are"
            clause = c("dim", f" ({n_own} seeded {files} yours and not compared)")
        print(c("bold_green", "status: clean") +
              " -- everything ccs syncs matches; "
              "nothing to collect, nothing to apply" + clause)
    else:
        # Both-sides drift needs `merge`. Recommending collect/apply here is
        # not merely incomplete -- they are ONE-WAY overwrites, so following
        # that advice discards whichever side loses. That is exactly how 50
        # lines of CLAUDE.md went missing before this verb existed.
        two_way = [rel for kinds in classified.values()
                   for rel, (k, _) in kinds.items() if k in ("two-sided", "no base")]
        unattributed = [rel for kinds in classified.values()
                        for rel, (k, _) in kinds.items() if k == "differs"]
        head = c("bold_yellow", f"status: drift in {render.n_entries(len(diffs))}")
        if two_way:
            differ = "differs" if len(two_way) == 1 else "differ"
            print(head + f" -- {render.n_files(len(two_way))} {differ} on "
                  + c("bold_red", "BOTH sides") + "; run " + c("bold", "ccs merge")
                  + " for those " + c("dim", "(collect/apply would overwrite one side; "
                                              "--long shows which and why)"))
            print("        " + c("dim", "one-sided drift is safe with ")
                  + c("bold", "ccs collect") + c("dim", " (live -> checkout) or ")
                  + c("bold", "ccs apply") + c("dim", " (checkout -> live)"))
        elif unattributed:
            differ = "differs" if len(unattributed) == 1 else "differ"
            print(head + f" -- {render.n_files(len(unattributed))} {differ}, and with no git "
                  "history ccs cannot tell which side changed; review before collect/apply")
        else:
            print(head + " -- run " + c("bold", "ccs diff")
                  + " to see which files, then " + c("bold", "ccs collect")
                  + " (live -> checkout) or " + c("bold", "ccs apply")
                  + " (checkout -> live)")


def _never_crash_on_content() -> None:
    """`ccs diff <path>` prints file CONTENT, and config files carry emoji.
    On a cp1252 Windows console that is UnicodeEncodeError -- a crash, from
    a read-only verb, on the user's own file. Keep the console's encoding
    (switching to UTF-8 would mojibake legacy cmd) but degrade unencodable
    characters to '?' instead of dying. Capture streams in tests lack
    reconfigure(); that is fine."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _never_crash_on_content()
    raw_argv = list(sys.argv[1:]) if argv is None else list(argv)
    split = _split_git_passthrough(raw_argv)
    if split is not None:
        return _run_git_verb(*split)
    args = _build_parser().parse_args(raw_argv)
    if args.verb == "git":
        # Unreachable in practice (the pre-dispatch above intercepts every
        # well-formed `git` run); kept so the subparser stays honest.
        seen = {}
        if getattr(args, "checkout_dir", None):
            seen["--checkout-dir"] = args.checkout_dir
        if getattr(args, "no_color", False):
            seen["--no-color"] = True
        return _run_git_verb(seen, list(args.gitargs or []))
    render.init(getattr(args, "no_color", False))
    # setup and doctor run BEFORE _setup(): their whole purpose is working
    # on an environment that is not configured yet, which is exactly the
    # state _setup() refuses.
    if args.verb == "setup":
        if getattr(args, "what", None) is None:
            # Bare `ccs setup` lands you somewhere useful instead of a usage
            # error: the doctor check IS the "how is setup going" overview,
            # with the command that fixes each gap (user request, 2026-08-25
            # -- the csb setup interaction shape).
            return _doctor(args)
        return _setup_box(args)
    if args.verb == "doctor":
        return _doctor(args)
    try:
        manifest, checkout, roots, repo = _setup(args)

        # REMOTE. One fetch per run, shared by status and the verbs, so the
        # branch line is a claim about the remote rather than about the last
        # fetch (the first round trip read "in sync with origin/main" only
        # because the operator had fetched by hand minutes earlier). Fetch
        # touches remote-tracking refs only; a failure is reported, never
        # fatal -- ccs must keep working offline.
        cfg = userconfig.load(roots.get('USER_CLAUDE'))
        # BOX. What this machine declares itself to be (~/claude/ccs-box.json).
        # Tag-gated entries apply and collect only where every tag is
        # declared; a missing or broken file means no tags, never all.
        box = boxconfig.load(roots.get('USER_CLAUDE'))
        for err in box.errors:
            print(c('yellow', f'warning: box config: {err}'))
        if args.verb == "seed":
            args._box_tags = box.tags
            return _seed_verb(args, manifest, checkout, roots, repo)
        fetched, fetch_detail, behind = (render.UNSPECIFIED, "", None)
        pulled: tuple[int, bool, str] | None = None
        if args.verb in ("status", "collect", "apply"):
            fetched, fetch_detail, behind = _remote_state(repo, cfg, args)

        # AUTO-PULL (status only, opt-in). The pull runs BEFORE the file
        # comparison below, so the drift table describes post-pull reality
        # in the same run. Only after a fetch that succeeded THIS run --
        # fast-forwarding onto stale knowledge answers a question nobody
        # asked -- and strictly --ff-only: divergence and dirty files are
        # reported in git's own words, never resolved on the user's behalf.
        if args.verb == "status" and behind and repo is not None:
            want = getattr(args, "pull", None)
            if want is None:
                want = bool(cfg.get("auto_pull"))
            if want and fetched is True:
                ahead, _ = repo.ahead_behind()
                if ahead:
                    pulled = (behind, False,
                              f"{ahead} local commit(s) the remote lacks -- diverged")
                else:
                    ok, msg = repo.ff_update()
                    pulled = (behind, ok, "" if ok else msg)
                    if ok:
                        behind = 0  # the drift verdict below is post-pull truth

        if args.verb in ("collect", "apply") and behind:
            # A one-way verb against a checkout that is behind installs or
            # stages content the remote has already superseded. Nothing is
            # LOST either way, and "sync what I have here, now" is a
            # legitimate intent -- so the default is to say so and proceed;
            # require_current turns it into a refusal for users who want the
            # pull-first loop enforced. A FAILED fetch never refuses: refusing
            # to work because the network is down would defeat the tool.
            strict = getattr(args, "require_current", False) or bool(cfg.get("require_current"))
            what = ("applying what is here; git pull first for the other machine's latest"
                    if args.verb == "apply" else
                    "collecting onto a stale base; git pull before you push")
            msg = f"checkout is {behind} behind {repo.upstream()}"
            if strict:
                print(c("bold_red", "REFUSING") + f": {msg} -- git pull first "
                      + c("dim", "(--require-current is set)"))
                return EXIT_DRIFT
            print(c("yellow", "note") + f": {msg} -- {what}")
        elif args.verb in ("collect", "apply") and fetched is False:
            print(c("dim", f"note: could not fetch {repo.upstream() or 'upstream'}"
                           f" ({fetch_detail}); pull status unknown, proceeding"))

        # GUARD both one-way verbs. `collect`/`apply` copy `modified` files
        # in the same breath as the safe one-sided ones; for a genuinely
        # two-way file that discards the losing side and reports success.
        wrong_dir: dict[str, str] = {}
        if args.verb in ("collect", "apply") and not getattr(args, "force", False):
            risky = merge.two_way_labels(manifest, checkout, roots, box.tags,
                                         only=getattr(args, "only", None))
            # DIRECTION. A one-sided file is safe for ONE verb, not both:
            # live-ahead has nothing to apply (apply would revert the user's
            # edits); checkout-ahead has nothing to collect (collect would undo
            # the other machine's work). Attribute each differing file through
            # the same infer_base the guard uses and skip the wrong direction.
            # Measured 2026-08-21: 3 live-ahead and 22 checkout-ahead files on
            # one machine -- either verb alone would have clobbered one set.
            if repo is not None:
                for d in diff_all(manifest, checkout, roots, box.tags):
                    if d.mismatch or not d.modified:
                        continue
                    reached, sub = only_scope(getattr(args, "only", None), d.entry.repo)
                    if not reached:
                        continue
                    for rel in d.modified:
                        if not rel_in_scope(rel, sub):
                            continue
                        kind, evidence = _classify(checkout, d, rel)
                        key = (f"{d.entry.target}/{rel}" if rel else d.entry.target) \
                            if args.verb == "apply" else \
                            (f"{d.entry.repo}/{rel}" if rel else d.entry.repo)
                        if args.verb == "apply" and (
                                (kind == "one-sided" and evidence.startswith("live ahead"))
                                or kind == "local snap"):
                            wrong_dir[key] = ("live is ahead -- nothing to apply; `ccs collect` it"
                                              if kind == "one-sided" else
                                              "checkout copy is an uncommitted local snapshot, older than live")
                        elif args.verb == "collect" and kind == "one-sided" \
                                and evidence.startswith("checkout ahead"):
                            wrong_dir[key] = "checkout is ahead -- nothing to collect; `ccs apply` it"
            # --only is applied inside two_way_labels, component-wise, sub-entry
            # included -- the old post-filter on the last path component
            # ("skills" for --only dotclaude/skills) could not express a
            # subtree and matched by accident on a shared last component.
            if risky:
                print(c("bold_red", "REFUSING") + ": " +
                      f"{render.n_files(len(risky))} changed on BOTH sides -- "
                      f"a one-way {args.verb} would discard one side's work")
                for r in risky:
                    print(f"  {c('magenta', r)}")
                print(c("dim", "  run `ccs merge` for these, or `--force` to "
                               "overwrite anyway (destructive)"))
                return EXIT_DRIFT

        if args.verb == "collect":
            r = collect(manifest, checkout, roots, repo=repo, dry_run=args.dry_run,
                        only=args.only, add=args.add, skip=wrong_dir,
                        box_tags=box.tags)
            for rel, why in r.skipped:
                print(f"{c('dim', 'skipped')} {rel} {c('dim', '-- ' + why)}")
            for rel, pattern in r.refused_denied:
                print(f"{c('magenta', 'protected')} {rel} "
                      f"{c('dim', f'-- matches deny rule {pattern!r}, stays local')}")
            for rel in r.denied_live:
                print(f"{c('magenta', 'protected')} {rel} "
                      f"{c('dim', '-- matches a deny rule, stays local')}")
            for hit in r.refused_secrets:
                # Built on its own line: a replacement field may not span
                # lines in a non-triple-quoted f-string before 3.12 (PEP 701).
                detail = f"-- looks like a credential (line {hit.line_no}: {hit.excerpt})"
                print(f"{c('bold_red', 'REFUSED')} {hit.rel_path} {c('red', detail)}")
            for rel in r.copied:
                verb = "would copy" if args.dry_run else "copied"
                print(f"{c('green', verb)}: {rel}")
            for rel in r.missing_live:
                print(f"{c('dim', 'in the checkout but not live (left alone):')} {rel}")
            for rel in r.git_ignored:
                print(c("bold_red", "ERROR") + f": copied but IGNORED by git: {rel} "
                      + c("dim", "-- it would silently never commit; check "
                                 ".gitignore and .git/info/exclude"))
            for path, reason in r.failed:
                print(c("bold_red", "FAILED") + f" ({reason}): {path}")
            for m in r.mismatched:
                print(c("bold_red", "ERROR") + f": {m} -- entry skipped, fix the live tree")
            if r.git_ignored or r.failed or r.mismatched:
                return EXIT_ERROR
            if args.only and r.only_matched == 0:
                _warn_only_miss(args, manifest, box)
            for rel in r.adopted_entries:
                print(c("cyan", "ADOPTING") +
                      f": {rel} -- the checkout carried nothing here, so its files "
                      "are being added")
            if r.withheld_additions:
                n = len(r.withheld_additions)
                print(c("yellow", "WITHHELD") +
                      f": {n} file{'s' if n != 1 else ''} the checkout does not have yet "
                      "-- NOT copied")
                for rel in r.withheld_additions:
                    print(f"    {rel}")
                print(c("dim", "    these would be NEW in the payload; pass --add to "
                               "include them, or exclude them for good in "
                               "ccs-manifest.json's collect_exclude"))
            if not r.copied and not r.refusals:
                print(c("green", "collect: nothing to do") +
                      " -- the checkout already has everything from your live config")
            # Deny-list skips are the guard WORKING (intended state, exit 0);
            # credential-shaped content in allowlisted files is an alarm (exit 1).
            return EXIT_DRIFT if r.refused_secrets else EXIT_CLEAN

        if args.verb == "apply":
            backups = roots["USER_CLAUDE"] / "backups" / "ccs"
            r = apply(manifest, checkout, roots, backups, repo=repo,
                      dry_run=args.dry_run, only=args.only,
                      sync_removals=args.sync_removals,
                      skip=wrong_dir, box_tags=box.tags,
                      reseed=getattr(args, "reseed", None))
            for rel, why in r.skipped:
                print(f"{c('dim', 'skipped')} {rel} {c('dim', '-- ' + why)}")
            for rel, pattern in r.refused_denied:
                print(c("bold_red", "REFUSED") +
                      f" (deny-list {pattern} -- remove it from the "
                      f"payload repo): {rel}")
            for rel in r.copied:
                verb = "would apply" if args.dry_run else "applied"
                print(f"{c('green', verb)}: {rel}")
            # Same dry-run honesty as r.copied above: a message that claims
            # completion while nothing was written is exactly the
            # overclaiming this release removes elsewhere (tester finding,
            # v0.5.2 checklist run-01).
            for rel in r.reseeded:
                if args.dry_run:
                    print(f"{c('cyan', 'would reseed')} {rel} "
                          f"{c('dim', '-- old copy to the backup dir first')}")
                else:
                    print(f"{c('cyan', 'reseeded')} {rel} "
                          f"{c('dim', '-- previous copy backed up; the fresh seed is live')}")
            for rel in r.seeded:
                verb = "would seed" if args.dry_run else "seeded"
                print(f"{c('green', verb)} {rel} {c('dim', '-- was absent locally')}")
            for rel in r.removals_staged:
                print(f"{c('yellow', 'removal staged to backup')}: {rel}")
            for rel in r.local_only:
                print(f"{c('dim', 'local only')} {rel} "
                      + c("dim", "-- new here, never in the checkout; left alone"))
            for rel in r.removals_pending:
                print(f"{c('yellow', 'removal PENDING')}: {rel} "
                      + c("dim", "-- local file not in the checkout; "
                                 "`ccs collect` to keep it, --sync-removals to stage it away"))
            for e in r.deferred:
                print(c("dim", f"skipped (strategy lands in Phase 2): {e}"))
            for path, reason in r.failed:
                print(c("bold_red", "FAILED") + f" ({reason}): {path}")
            for m in r.mismatched:
                print(c("bold_red", "ERROR") + f": {m} -- entry skipped, fix the live tree")
            if r.backup_dir:
                print(c("dim", f"backups: {r.backup_dir}"))
            if args.only and r.only_matched == 0:
                _warn_only_miss(args, manifest, box)
            rs = getattr(args, "reseed", None)
            if rs and not r.reseeded and not r.failed                     and rs.replace(chr(92), "/") not in (t.replace(chr(92), "/")
                                                         for t in r.seeded):
                print(c("yellow", f"warning: --reseed {rs!r} matched no seed "
                                  "entry with an existing live file (nothing done)"))
            if not (r.copied or r.seeded or r.reseeded or r.removals_staged or r.refused_denied):
                print(c("green", "apply: nothing to do") +
                      " -- your live config already matches the checkout")
            if r.failed or r.mismatched:
                return EXIT_ERROR
            # A denied file IN THE PAYLOAD is an anomaly (unlike collect's
            # live-side denials, which are the guard working as intended):
            # someone committed a never-sync file to the repo. Exit 1 until
            # it is removed there.
            return EXIT_DRIFT if (r.removals_pending or r.refused_denied) \
                else EXIT_CLEAN

        if args.verb == "merge":
            blob, label = _supplied_base(args)
            r = merge.run(manifest, checkout, roots, tool=args.tool,
                          dry_run=args.dry_run, accept=args.accept, only=args.only,
                          union=args.union, launch_tool=not args.no_launch,
                          preview=args.preview, base_mode=args.base,
                          base_override=blob, base_label=label,
                          cod_ratio=args.block_swap_ratio)
            for item in (i for i in r.resolved + r.previewed + [i for i, _ in r.unresolved]
                         if i.base_supplied and i.cod is not None):
                st = item.cod
                print(c("cyan", f"supplied base {item.base_label}: {item.label}")
                      + c("dim", f" -- {st.hunks} hunk(s) to review ({st.natural} natural + "
                                 f"{st.wrapped} wrapped from {st.regions} region(s) the payload "
                                 f"removed since the base)"))
            for item in r.refused:
                print(f"{c('yellow', 'refused')} {item.label} {c('dim', '-- ' + item.reason)}")
            for item in r.planned:
                print(f"{c('magenta', 'would merge')}: {item.label}")
                if repo is not None:
                    _print_base_table(item, checkout, roots, (blob, label),
                                      args.block_swap_ratio)
            for item in r.siblings:
                sib, sha, n, ratio = item.sibling
                used = " (USED as --base sibling)" if item.base == sib else ""
                print(f"{c('yellow', 'nearest historical version')}: {sha}{used}")
                print(c("dim", f"    {sib}"))
                print(c("dim", f"    not an ancestor -- it attributes {n} purely "
                               f"deleted line(s) to you that the other side still "
                               f"has ({ratio:.0%}); open it to see what changed "
                               "since, but do not merge against it blindly"))
            for item in r.no_base:
                print(f"{c('yellow', 'no base')} {item.label} "
                      + c("dim", "-- 2-way hand-off; the base pane is empty"))
            for item in r.previewed:
                print(f"{c('cyan', 'previewed')}: {item.label} "
                      + c("dim", "-- nothing validated, nothing installed"))
            for item in r.resumed:
                print(f"{c('dim', 'resumed')} {item.label} -- kept your prior edits")
            loss_by_item = {id(i): v for i, v in r.accepted_with_loss}
            honoured_by_item = {id(i): v for i, v in r.honoured}
            adopted = {id(i) for i in r.adopted}
            for item in r.resolved:
                verb = "merged and installed" if args.accept else "merged (not installed)"
                if id(item) in adopted:
                    verb = "merged and installed LIVE ONLY"
                print(f"{c('green', verb)}: {item.label}")
                if id(item) in adopted:
                    print(c("yellow", f"    adoption merge: checkout left at HEAD; record "
                                      f"{item.base_label} as this box's base for {item.label}"))
                v = honoured_by_item.get(id(item))
                if v is not None:
                    _print_honoured(v)
                v = loss_by_item.get(id(item))
                if v is not None:
                    n = sum(len(x) for x in v.lost.values())
                    print(c("yellow", f"    with {n} line(s) dropped on your say-so "
                                      "(no base; you reviewed the file)"))
            for item, v in r.unresolved:
                print(f"{c('bold_red', 'NOT INSTALLED')} {item.label}")
                for f in v.failures:
                    print("    " + c("red", f))
                # Users who did not author both sides cannot know which to
                # keep. Offer the cheap deterministic signals first, and only
                # then the paid one.
                for h in merge.resolution_hints(item):
                    print("    " + c("yellow", "hint") + " " + c("dim", h))
                print("    " + c("dim", "still stuck? ") + c("bold", "--ai") +
                      c("dim", " proposes a resolution you review in the same "
                               "3-way view (set ai_merge_command in "
                               "~/claude/ccs-config.json; a local model works "
                               "and costs nothing)"))
            if r.backup_dir:
                print(c("dim", f"originals backed up: {r.backup_dir}"))
            if r.workspace:
                print(c("dim", f"workspace: {r.workspace}"))
            # r.previewed counts: a preview resolves nothing by design, so
            # reporting "nothing to do" right after listing a previewed file
            # contradicts the line printed immediately above it.
            if not (r.refused or r.planned or r.resolved
                    or r.unresolved or r.previewed):
                print(c("green", "merge: nothing to do")
                      + " -- no file differs on both sides")
            # Validation failure is the alarm this whole verb exists for: a
            # tool exiting 0 is NOT evidence the merge kept both sides.
            if r.unresolved:
                return merge.EXIT_VALIDATION
            return EXIT_DRIFT if r.refused else EXIT_CLEAN

        # status / diff
        all_diffs = diff_all(manifest, checkout, roots, box.tags)
        diffs = [d for d in all_diffs if not d.clean]
        if args.verb == "status":
            over = {}
            if getattr(args, 'long', False):
                over['status_detail'] = 'long'
            elif getattr(args, 'compact', False):
                over['status_detail'] = 'compact'
            _print_status(checkout, repo, roots, all_diffs, diffs,
                          userconfig.load(roots.get('USER_CLAUDE'), over),
                          remote=(fetched, fetch_detail, behind),
                          manifest=manifest, box=box, pulled=pulled)
            # Behind the upstream is drift too: the checkout is not the
            # latest the fleet has, even when live matches it exactly.
            return EXIT_CLEAN if not diffs and not behind else EXIT_DRIFT
        else:
            # `ccs diff <path>` shows the CONTENT, not just the filename.
            # "merged and installed" is a claim; this is how you check it.
            wanted = getattr(args, "path", None)
            if wanted:
                ways = getattr(args, 'difftool', None)
                if ways:
                    return _launch_file_difftool(all_diffs, wanted,
                                                 getattr(args, 'tool', None),
                                                 ways=ways, checkout=checkout,
                                                 roots=roots, repo=repo,
                                                 supplied=_supplied_base(args))
                return _print_file_diff(all_diffs, wanted, manifest, box)
            for d in diffs:
                if d.mismatch:
                    print(c("red", f"mismatch:   {d.entry.repo} ({d.mismatch})"))
                    continue
                for rel in d.live_only:
                    where = f"{d.entry.repo}/{rel}" if rel else d.entry.repo
                    print(f"{c('yellow', 'live-only:')}  {where}")
                for rel in d.repo_only:
                    where = f"{d.entry.repo}/{rel}" if rel else d.entry.repo
                    print(f"{c('cyan', 'repo-only:')}  {where}")
                for rel in d.modified:
                    where = f"{d.entry.repo}/{rel}" if rel else d.entry.repo
                    print(f"{c('magenta', 'modified: ')}  {where}")
            if not diffs:
                print(c("green", "no differences") +
                      " -- live config and the checkout match")
            else:
                print()
                print(c("dim", "live-only = only in your live config (ccs collect saves it) | "
                               "repo-only = only in the checkout (ccs apply installs it) | "
                               "modified = differs on both sides -- ccs merge, NOT collect/apply"))
            return EXIT_CLEAN if not diffs else EXIT_DRIFT

    except ApplyConflictError as e:
        print(f"ccs: {e}", file=sys.stderr)
        return EXIT_ERROR
    except merge.MergeError as e:
        print(f"ccs: {e}", file=sys.stderr)
        return merge.EXIT_NO_TOOL if "merge tool" in str(e) else EXIT_ERROR
    except (ManifestError, GitError, GitopsSafetyError) as e:
        print(f"ccs: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
