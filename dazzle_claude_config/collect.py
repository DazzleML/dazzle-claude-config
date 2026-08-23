"""collect: live tree -> payload checkout, through the guard stack.

Pipeline per file: allowlist (manifest entry) -> collect_exclude ->
deny-list (manifest deny + HARD_DENY, refusals REPORTED) -> secret scan
(refusals reported) -> copy. Afterwards, A8: verify the copied paths are
not ignored/excluded by git (machine-level info/exclude injections).
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .gitops import CheckoutRepo
from .manifest import Manifest
from .secrets import SecretHit, is_denied, scan_file
from .syncmap import diff_all, only_scope, scope_diff


@dataclass
class CollectResult:
    copied: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (path, reason): wrong direction
    refused_denied: list[tuple[str, str]] = field(default_factory=list)   # (rel, pattern)
    refused_secrets: list[SecretHit] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    missing_live: list[str] = field(default_factory=list)  # in repo, gone locally (report only)
    git_ignored: list[str] = field(default_factory=list)   # A8 violations
    denied_live: list[str] = field(default_factory=list)   # deny-matched, never sync
    failed: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)
    mismatched: list[str] = field(default_factory=list)    # type-conflict entries
    only_matched: int = 0                                  # entries passing --only
    withheld_additions: list[str] = field(default_factory=list)  # new paths, needs --add
    adopted_entries: list[str] = field(default_factory=list)     # first-run entries

    @property
    def refusals(self) -> int:
        return len(self.refused_denied) + len(self.refused_secrets)


def collect(manifest: Manifest, checkout: Path, roots: dict[str, Path],
            repo: CheckoutRepo | None = None, dry_run: bool = False,
            only: str | None = None, add: bool = False,
            skip: dict[str, str] | None = None,
            box_tags=frozenset()) -> CollectResult:
    result = CollectResult()
    copied_repo_rels: list[str] = []

    for d in diff_all(manifest, checkout, roots, box_tags):
        reached, sub = only_scope(only, d.entry.repo)
        if not reached:
            continue
        d = scope_diff(d, sub)
        result.only_matched += 1
        if d.mismatch:
            result.mismatched.append(f"{d.entry.repo}: {d.mismatch}")
            continue
        result.excluded.extend(f"{d.entry.repo}/{r}" for r in d.excluded)
        result.denied_live.extend(f"{d.entry.target}/{r}" for r in d.denied_live)
        result.missing_live.extend(f"{d.entry.repo}/{r}" if r else d.entry.repo
                                   for r in d.repo_only)

        # Additive gating (DWP-7). Creating a NEW path in the checkout is the
        # step that becomes publication once someone runs `git push`; updating
        # a path the checkout already tracks is routine. ccs cannot see the
        # push, so it guards what gets staged instead.
        #
        # BUT the risk belongs to the PAYLOAD, not the verb: a private
        # single-user repo wants new files picked up silently, which is what
        # collect has always done and what most of the suite specifies. Only a
        # payload that knows it is published (a public collection, a shared
        # team repo) needs additions held back. So the policy is declared in
        # ccs-manifest.json and defaults to permissive -- existing checkouts
        # behave exactly as before.
        #
        # Adoption exemption: an entry the checkout tracks nothing for is a
        # first run (DWP-4 -- adoption CREATES the base), where every file is
        # necessarily an addition and refusing them all would make the first
        # collect a silent no-op.
        additions = list(d.live_only)
        if additions and manifest.hold_additions and not add:
            if d.repo_tracked:
                result.withheld_additions.extend(
                    f"{d.entry.repo}/{r}" if r else d.entry.repo for r in additions)
                additions = []
            else:
                result.adopted_entries.append(d.entry.repo)

        for rel in additions + d.modified:
            src = d.live_base / rel if rel else d.live_base
            display = f"{d.entry.repo}/{rel}" if rel else d.entry.repo
            if skip and display in skip:
                # The CHECKOUT is ahead here: live never changed since an older
                # commit. Copying live over it would undo the other machine's work.
                result.skipped.append((display, skip[display]))
                continue

            pattern = is_denied(rel or src.name, manifest.deny)
            if pattern:
                result.refused_denied.append((display, pattern))
                continue
            hits = scan_file(src, display)
            if hits:
                result.refused_secrets.extend(hits)
                continue

            if not dry_run:
                try:
                    dest = d.repo_base / rel if rel else d.repo_base
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                except OSError as e:
                    result.failed.append((display, str(e)))
                    continue
            result.copied.append(display)
            repo_rel = f"{d.entry.repo}/{rel}" if rel else d.entry.repo
            copied_repo_rels.append(repo_rel)

    # A8: copied files must be visible to git, or they will silently drop
    # out of the payload (Phase 0 incident: unanchored CLAUDE.md in a
    # machine-level info/exclude).
    if repo is not None and copied_repo_rels and not dry_run:
        result.git_ignored = repo.check_ignored(copied_repo_rels)

    return result
