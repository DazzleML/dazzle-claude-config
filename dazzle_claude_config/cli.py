"""ccs command-line interface.

Exit codes (A7): 0 = clean/success, 1 = drift or refusals present, 2 = error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import _version
from .apply import ApplyConflictError, apply
from .collect import collect
from .gitops import CheckoutRepo, GitError, GitopsSafetyError
from .manifest import Manifest, ManifestError
from .platform_info import default_checkout_dir, territory_roots
from .syncmap import diff_all

EXIT_CLEAN, EXIT_DRIFT, EXIT_ERROR = 0, 1, 2


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ccs",
        description="Sync Claude Code configuration across machines "
                    "(dazzle-claude-config).")
    p.add_argument("--version", action="version",
                   version=f"ccs {_version.DISPLAY_VERSION}")
    p.add_argument("--checkout-dir", default=None,
                   help="payload checkout location (default: ~/claude/dazzle-claude-code-config)")
    p.add_argument("--claude-dir", default=None,
                   help="override ~/.claude (also honors CLAUDE_CONFIG_DIR)")
    p.add_argument("--user-claude", default=None, help="override ~/claude")
    sub = p.add_subparsers(dest="verb", required=True)

    for verb, doc in (("collect", "copy live config INTO the checkout (guarded)"),
                      ("apply", "copy checkout config INTO the live tree (backed up)"),
                      ("status", "three-way drift report (exit 1 when drift)"),
                      ("diff", "list per-file differences")):
        sp = sub.add_parser(verb, help=doc)
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
            "or pass --checkout-dir)")
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


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        manifest, checkout, roots, repo = _setup(args)

        if args.verb == "collect":
            r = collect(manifest, checkout, roots, repo=repo, dry_run=args.dry_run)
            for rel, pattern in r.refused_denied:
                print(f"REFUSED (deny-list {pattern}): {rel}")
            for hit in r.refused_secrets:
                print(f"REFUSED (credential-shaped, line {hit.line_no}, "
                      f"{hit.excerpt}): {hit.rel_path}")
            for rel in r.copied:
                print(f"{'would copy' if args.dry_run else 'copied'}: {rel}")
            for rel in r.missing_live:
                print(f"missing locally (in repo, not live -- not touched): {rel}")
            for rel in r.git_ignored:
                print(f"ERROR: copied but IGNORED by git (A8 -- check "
                      f".git/info/exclude and .gitignore): {rel}")
            for path, reason in r.failed:
                print(f"FAILED ({reason}): {path}")
            for m in r.mismatched:
                print(f"ERROR: {m} -- entry skipped, fix the live tree")
            if r.git_ignored or r.failed or r.mismatched:
                return EXIT_ERROR
            if not r.copied and not r.refusals:
                print("collect: nothing to do")
            return EXIT_DRIFT if r.refusals else EXIT_CLEAN

        if args.verb == "apply":
            backups = roots["USER_CLAUDE"] / "backups" / "ccs"
            r = apply(manifest, checkout, roots, backups, repo=repo,
                      dry_run=args.dry_run, only=args.only,
                      sync_removals=args.sync_removals)
            for rel in r.copied:
                print(f"{'would apply' if args.dry_run else 'applied'}: {rel}")
            for rel in r.seeded:
                print(f"seeded (was absent): {rel}")
            for rel in r.removals_staged:
                print(f"removal staged to backup: {rel}")
            for rel in r.removals_pending:
                print(f"removal PENDING (local file not in repo; use "
                      f"--sync-removals or collect it): {rel}")
            for e in r.deferred:
                print(f"skipped (strategy lands in Phase 2): {e}")
            for path, reason in r.failed:
                print(f"FAILED ({reason}): {path}")
            for m in r.mismatched:
                print(f"ERROR: {m} -- entry skipped, fix the live tree")
            if r.backup_dir:
                print(f"backups: {r.backup_dir}")
            if args.only and r.only_matched == 0:
                print(f"warning: --only {args.only!r} matched no manifest entries")
            if not (r.copied or r.seeded or r.removals_staged):
                print("apply: nothing to do")
            if r.failed or r.mismatched:
                return EXIT_ERROR
            return EXIT_DRIFT if r.removals_pending else EXIT_CLEAN

        # status / diff
        diffs = [d for d in diff_all(manifest, checkout, roots) if not d.clean]
        if args.verb == "status":
            if repo is not None:
                print(f"checkout: {checkout} [{repo.branch_info()}]")
                if repo.has_conflicts():
                    print("MERGE CONFLICTS in checkout -- resolve before apply")
            for d in diffs:
                if d.mismatch:
                    print(f"{d.entry.repo}: {d.mismatch}")
                    continue
                print(f"{d.entry.repo}: {len(d.live_only)} live-only, "
                      f"{len(d.repo_only)} repo-only, {len(d.modified)} modified")
            print("status: clean" if not diffs else
                  f"status: drift in {len(diffs)} entr{'y' if len(diffs)==1 else 'ies'}")
            return EXIT_CLEAN if not diffs else EXIT_DRIFT
        else:
            for d in diffs:
                if d.mismatch:
                    print(f"mismatch:   {d.entry.repo} ({d.mismatch})")
                    continue
                for rel in d.live_only:
                    print(f"live-only:  {d.entry.repo}/{rel}" if rel
                          else f"live-only:  {d.entry.repo}")
                for rel in d.repo_only:
                    print(f"repo-only:  {d.entry.repo}/{rel}" if rel
                          else f"repo-only:  {d.entry.repo}")
                for rel in d.modified:
                    print(f"modified:   {d.entry.repo}/{rel}" if rel
                          else f"modified:   {d.entry.repo}")
            return EXIT_CLEAN if not diffs else EXIT_DRIFT

    except ApplyConflictError as e:
        print(f"ccs: {e}", file=sys.stderr)
        return EXIT_ERROR
    except (ManifestError, GitError, GitopsSafetyError) as e:
        print(f"ccs: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
