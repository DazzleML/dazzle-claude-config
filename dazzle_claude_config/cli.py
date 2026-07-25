"""ccs command-line interface.

Exit codes (A7): 0 = clean/success, 1 = drift or refusals present, 2 = error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import _version, render
from .apply import ApplyConflictError, apply
from .collect import collect
from .gitops import CheckoutRepo, GitError, GitopsSafetyError
from .manifest import Manifest, ManifestError
from .platform_info import default_checkout_dir, territory_roots
from .render import c
from .syncmap import diff_all

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
    p = argparse.ArgumentParser(
        prog="ccs",
        description="Sync Claude Code configuration across machines "
                    "(dazzle-claude-config).")
    p.add_argument("--version", action="version",
                   version=f"ccs {_version.DISPLAY_VERSION}")
    _add_common(p)
    sub = p.add_subparsers(dest="verb", required=True)

    for verb, doc in (("collect", "copy live config INTO the checkout (guarded)"),
                      ("apply", "copy checkout config INTO the live tree (backed up)"),
                      ("status", "three-way drift report (exit 1 when drift)"),
                      ("diff", "list per-file differences")):
        sp = sub.add_parser(verb, help=doc)
        _add_common(sp, suppress=True)
        if verb in ("collect", "apply"):
            sp.add_argument("--dry-run", action="store_true")
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


def _print_status(checkout, repo, roots, all_diffs, diffs) -> None:
    """The status report, written for someone who has not read the docs.

    Three legs, because that is what "in sync" actually means here:
    live vs checkout (the entries), checkout vs remote (branch tracking),
    and the checkout's own uncommitted work (git's territory, not ours).
    """
    files = sum(d.total for d in all_diffs)
    print(f"{c('bold', 'checkout')}  {c('cyan', str(checkout))}")
    if repo is not None:
        print(f"          {c('dim', render.humanize_branch(repo.branch_info()))}")
        dirty = len([l for l in repo.porcelain() if l.strip()])
        if dirty:
            print(f"          {c('yellow', f'{dirty} uncommitted change'
                                           f'{"" if dirty == 1 else "s"} in the checkout')}"
                  f" {c('dim', '-- commit and push to share with your other machines')}")
        if repo.has_conflicts():
            print(c("bold_red", "          MERGE CONFLICTS -- resolve them before `ccs apply`"))
    print(f"{c('bold', 'compared')}  {render.n_files(files)} across "
          f"{render.n_entries(len(all_diffs))} of config")
    print(f"          {c('dim', str(roots['CLAUDE_DIR']))}")
    print(f"          {c('dim', str(roots['USER_CLAUDE']))}")

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
                bits.append(f"{n} differ{'s' if n == 1 else ''} on both sides")
            print(f"  {c('cyan', d.entry.repo)}: {', '.join(bits)}")

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
        print(c("bold_yellow", f"status: drift in {render.n_entries(len(diffs))}") +
              " -- run " + c("bold", "ccs diff") + " to see which files, then " +
              c("bold", "ccs collect") + " (live -> checkout) or " +
              c("bold", "ccs apply") + " (checkout -> live)")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    render.init(getattr(args, "no_color", False))
    try:
        manifest, checkout, roots, repo = _setup(args)

        if args.verb == "collect":
            r = collect(manifest, checkout, roots, repo=repo, dry_run=args.dry_run)
            for rel, pattern in r.refused_denied:
                print(f"{c('magenta', 'protected')} {rel} "
                      f"{c('dim', f'-- matches deny rule {pattern!r}, stays local')}")
            for rel in r.denied_live:
                print(f"{c('magenta', 'protected')} {rel} "
                      f"{c('dim', '-- matches a deny rule, stays local')}")
            for hit in r.refused_secrets:
                print(c("bold_red", "REFUSED") +
                      f" {hit.rel_path} {c('red', f'-- looks like a credential '
                                            f'(line {hit.line_no}: {hit.excerpt})')}")
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
                      sync_removals=args.sync_removals)
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

        # status / diff
        all_diffs = diff_all(manifest, checkout, roots)
        diffs = [d for d in all_diffs if not d.clean]
        if args.verb == "status":
            _print_status(checkout, repo, roots, all_diffs, diffs)
            return EXIT_CLEAN if not diffs else EXIT_DRIFT
        else:
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
                               "modified = differs on both sides"))
            return EXIT_CLEAN if not diffs else EXIT_DRIFT

    except ApplyConflictError as e:
        print(f"ccs: {e}", file=sys.stderr)
        return EXIT_ERROR
    except (ManifestError, GitError, GitopsSafetyError) as e:
        print(f"ccs: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
