"""ccs command-line interface.

Exit codes (A7): 0 = clean/success, 1 = drift or refusals present, 2 = error.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from pathlib import Path

from . import _version, merge, render
from .apply import ApplyConflictError, apply
from .collect import collect
from .gitops import CheckoutRepo, GitError, GitopsSafetyError
from .manifest import Manifest, ManifestError
from .platform_info import default_checkout_dir, territory_roots
from .render import c
from . import userconfig
from .syncmap import _normalize_eol, diff_all, files_differ, line_stats

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
  git -C <checkout> add -A && git commit && git push    # share it with your other machines

  A file that changed on BOTH sides needs `ccs merge`. `collect` and `apply`
  are ONE-WAY copies, so running either on such a file discards whatever the
  losing side added -- silently, and with a success message. `ccs status`
  marks those entries; `ccs merge --preview` shows you the three versions in
  your own diff tool before you commit to anything.

  Preferences (diff tool, status verbosity, AI merge command) live in
  ~/claude/ccs-config.json; each is also a per-run flag and an env var.

  A public collection to try: https://github.com/DazzleML/dazzle-claude-code-config
""")
    p.add_argument("--version", action="version",
                   version=f"ccs {_version.DISPLAY_VERSION}")
    _add_common(p)
    sub = p.add_subparsers(dest="verb", required=True)

    for verb, doc in (("collect", "copy live config INTO the checkout (guarded)"),
                      ("apply", "copy checkout config INTO the live tree (backed up)"),
                      ("merge", "resolve files that differ on BOTH sides, in your diff tool"),
                      ("status", "three-way drift report (exit 1 when drift)"),
                      ("diff", "list per-file differences")):
        sp = sub.add_parser(verb, help=doc)
        _add_common(sp, suppress=True)
        if verb in ("collect", "apply", "merge"):
            sp.add_argument("--dry-run", action="store_true")
        if verb in ("collect", "apply"):
            sp.add_argument("--force", action="store_true",
                            help="copy even files that changed on BOTH sides "
                                 "(DESTRUCTIVE -- discards the losing side; "
                                 "prefer ccs merge)")
        if verb == "collect":
            sp.add_argument("--only", default=None,
                            help="limit to entries whose repo path starts with this prefix")
            sp.add_argument("--add", action="store_true",
                            help="also copy files the checkout does not have yet "
                                 "(default: update tracked files only, so a collect "
                                 "never publishes something you did not ask for)")
        if verb == "merge":
            sp.add_argument("--tool", default=None,
                            help="git mergetool name (default: probe for one whose "
                                 "binary actually exists)")
            sp.add_argument("--only", default=None,
                            help="limit to entries whose repo path starts with this prefix")
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
        if verb == "status":
            g = sp.add_mutually_exclusive_group()
            g.add_argument("--long", action="store_true",
                           help="always list every differing file, ignoring the "
                                "line budget")
            g.add_argument("--compact", action="store_true",
                           help="one line per entry, never the per-file breakdown")
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
                            help="limit to entries whose repo path starts with this prefix")
            sp.add_argument("--sync-removals", action="store_true",
                            help="stage live-only files into the backup dir "
                                 "(default: report only)")
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


def _launch_file_difftool(all_diffs, wanted: str, tool: str | None, *,
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
        return _launch_three_way(lv, rp, target, repo_label, tool, checkout, roots, repo)
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


def _launch_three_way(lv, rp, target, repo_label, tool, checkout, roots, repo) -> int:
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


def _print_file_diff(all_diffs, wanted: str) -> int:
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


def _print_status(checkout, repo, roots, all_diffs, diffs, cfg=None) -> None:
    """The status report, written for someone who has not read the docs.

    Three legs, because that is what "in sync" actually means here:
    live vs checkout (the entries), checkout vs remote (branch tracking),
    and the checkout's own uncommitted work (git's territory, not ours).
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
    print(f"{c('bold', 'checkout')}  {c('cyan', str(checkout))}")
    if repo is not None:
        print(f"          {c('dim', render.humanize_branch(repo.branch_info()))}")
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

    cfg = cfg or {}
    detail = cfg.get("status_detail", "auto")
    budget = cfg.get("status_max_lines", 30)
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
    if not diffs:
        print(c("bold_green", "status: clean") +
              " -- your live config and the checkout match; "
              "nothing to collect, nothing to apply")
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
    args = _build_parser().parse_args(argv)
    render.init(getattr(args, "no_color", False))
    try:
        manifest, checkout, roots, repo = _setup(args)

        # GUARD both one-way verbs. `collect`/`apply` copy `modified` files
        # in the same breath as the safe one-sided ones; for a genuinely
        # two-way file that discards the losing side and reports success.
        wrong_dir: dict[str, str] = {}
        if args.verb in ("collect", "apply") and not getattr(args, "force", False):
            risky = merge.two_way_labels(manifest, checkout, roots)
            # DIRECTION. A one-sided file is safe for ONE verb, not both:
            # live-ahead has nothing to apply (apply would revert the user's
            # edits); checkout-ahead has nothing to collect (collect would undo
            # the other machine's work). Attribute each differing file through
            # the same infer_base the guard uses and skip the wrong direction.
            # Measured 2026-08-21: 3 live-ahead and 22 checkout-ahead files on
            # one machine -- either verb alone would have clobbered one set.
            if repo is not None:
                for d in diff_all(manifest, checkout, roots):
                    if d.mismatch or not d.modified:
                        continue
                    for rel in d.modified:
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
            # Both verbs carry --only since 0.4.0; getattr stays as armor
            # against the original 0.3.x bug, where reaching for args.only
            # unconditionally crashed collect while apply passed.
            only = getattr(args, "only", None)
            if only:
                risky = [r for r in risky if r.startswith(only.split("/")[-1])]
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
                        only=args.only, add=args.add, skip=wrong_dir)
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
                print(c("yellow", f"warning: --only {args.only!r} matched no manifest entries"))
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
                      skip=wrong_dir)
            for rel, why in r.skipped:
                print(f"{c('dim', 'skipped')} {rel} {c('dim', '-- ' + why)}")
            for rel, pattern in r.refused_denied:
                print(c("bold_red", "REFUSED") +
                      f" (deny-list {pattern} -- remove it from the "
                      f"payload repo): {rel}")
            for rel in r.copied:
                verb = "would apply" if args.dry_run else "applied"
                print(f"{c('green', verb)}: {rel}")
            for rel in r.seeded:
                print(f"{c('green', 'seeded')} {rel} {c('dim', '-- was absent locally')}")
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
                print(c("yellow", f"warning: --only {args.only!r} matched no manifest entries"))
            if not (r.copied or r.seeded or r.removals_staged or r.refused_denied):
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
            r = merge.run(manifest, checkout, roots, tool=args.tool,
                          dry_run=args.dry_run, accept=args.accept, only=args.only,
                          union=args.union, launch_tool=not args.no_launch,
                          preview=args.preview, base_mode=args.base)
            for item in r.refused:
                print(f"{c('yellow', 'refused')} {item.label} {c('dim', '-- ' + item.reason)}")
            for item in r.planned:
                print(f"{c('magenta', 'would merge')}: {item.label}")
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
            for item in r.resolved:
                verb = "merged and installed" if args.accept else "merged (not installed)"
                print(f"{c('green', verb)}: {item.label}")
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
        all_diffs = diff_all(manifest, checkout, roots)
        diffs = [d for d in all_diffs if not d.clean]
        if args.verb == "status":
            over = {}
            if getattr(args, 'long', False):
                over['status_detail'] = 'long'
            elif getattr(args, 'compact', False):
                over['status_detail'] = 'compact'
            _print_status(checkout, repo, roots, all_diffs, diffs,
                          userconfig.load(roots.get('USER_CLAUDE'), over))
            return EXIT_CLEAN if not diffs else EXIT_DRIFT
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
                                                 roots=roots, repo=repo)
                return _print_file_diff(all_diffs, wanted)
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
