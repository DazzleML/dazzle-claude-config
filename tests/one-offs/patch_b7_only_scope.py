"""One-off: B7 -- `--only` scopes to a sub-entry path, component-wise, in
every consumer (apply, collect, the two-way guard, merge, the miss
warnings). Asserts on the old text; run once."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "dazzle_claude_config"


def patch(name: str, pairs: list[tuple[str, str]]) -> None:
    p = ROOT / name
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        assert old in s, (name, old[:80])
        s = s.replace(old, new, 1)
    p.write_text(s, encoding="utf-8")


patch("syncmap.py", [
    ('''def entry_applies(entry: Entry, box_tags=frozenset()) -> bool:''',
     '''def only_scope(only: str | None, repo: str) -> tuple[bool, str | None]:
    """Does `--only` reach this entry, and how much of it?

    Component-wise, never a string prefix: `dotclaude/skills` reaches the
    entry `dotclaude/skills` and everything under `dotclaude/skills/...`, and
    does NOT reach `dotclaude/skills-extra`. Returns (reached, sub_prefix):

      (True, None)    the whole entry (`--only` names it or a parent of it)
      (True, "a/b")   only files under a/b inside the entry -- `--only
                      dotclaude/skills/test-mutation` on the entry
                      `dotclaude/skills`. Until 0.4.3 this matched nothing,
                      silently: the filter was `entry.repo.startswith(only)`.
      (False, None)   not reached
    """
    if not only:
        return True, None
    o = only.replace(chr(92), "/").strip("/")
    r = repo.strip("/")
    if o == r or r.startswith(o + "/"):
        return True, None
    if o.startswith(r + "/"):
        return True, o[len(r) + 1:]
    return False, None


def rel_in_scope(rel: str, sub_prefix: str | None) -> bool:
    """Is a file (entry-relative path) inside the --only sub-prefix?"""
    if sub_prefix is None:
        return True
    r = rel.replace(chr(92), "/")
    return r == sub_prefix or r.startswith(sub_prefix + "/")


def scope_diff(d: "EntryDiff", sub_prefix: str | None) -> "EntryDiff":
    """A copy of an EntryDiff with every per-file list cut to the sub-prefix
    -- so the guard, the direction skips, --force, and the removal
    candidates all see the same, scoped, set of files."""
    if sub_prefix is None:
        return d
    import dataclasses
    return dataclasses.replace(
        d,
        live_only=[r for r in d.live_only if rel_in_scope(r, sub_prefix)],
        repo_only=[r for r in d.repo_only if rel_in_scope(r, sub_prefix)],
        modified=[r for r in d.modified if rel_in_scope(r, sub_prefix)],
        excluded=[r for r in d.excluded if rel_in_scope(r, sub_prefix)],
        denied_live=[r for r in d.denied_live if rel_in_scope(r, sub_prefix)],
    )


def entry_applies(entry: Entry, box_tags=frozenset()) -> bool:'''),
])

patch("apply.py", [
    ('''from .syncmap import diff_all, entry_applies, entry_bases''',
     '''from .syncmap import diff_all, entry_applies, entry_bases, only_scope, scope_diff'''),
    ('''    for d in diff_all(manifest, checkout, roots, box_tags):
        if only and not d.entry.repo.startswith(only):
            continue
        result.only_matched += 1
        if d.mismatch:''',
     '''    for d in diff_all(manifest, checkout, roots, box_tags):
        reached, sub = only_scope(only, d.entry.repo)
        if not reached:
            continue
        d = scope_diff(d, sub)
        result.only_matched += 1
        if d.mismatch:'''),
    ('''        if only and not entry.repo.startswith(only):
            continue
        result.only_matched += 1
        live_base, repo_base = entry_bases(''',
     '''        reached, sub = only_scope(only, entry.repo)
        if not reached or sub is not None:      # a seed is one file; no sub-path
            continue
        result.only_matched += 1
        live_base, repo_base = entry_bases('''),
])

patch("collect.py", [
    ('''from .syncmap import diff_all''',
     '''from .syncmap import diff_all, only_scope, scope_diff'''),
    ('''    for d in diff_all(manifest, checkout, roots, box_tags):
        if only and not d.entry.repo.startswith(only):
            continue
        result.only_matched += 1''',
     '''    for d in diff_all(manifest, checkout, roots, box_tags):
        reached, sub = only_scope(only, d.entry.repo)
        if not reached:
            continue
        d = scope_diff(d, sub)
        result.only_matched += 1'''),
])

patch("merge.py", [
    ('''from .syncmap import EntryDiff, _normalize_eol, diff_all''',
     '''from .syncmap import EntryDiff, _normalize_eol, diff_all, only_scope, rel_in_scope, scope_diff'''),
    ('''def two_way_labels(manifest: Manifest, checkout: Path,
                   roots: dict[str, Path]) -> list[str]:''',
     '''def two_way_labels(manifest: Manifest, checkout: Path,
                   roots: dict[str, Path], box_tags=frozenset(),
                   only: str | None = None) -> list[str]:'''),
    ('''    out: list[str] = []
    for d in diff_all(manifest, checkout, roots):
        if d.mismatch:
            continue
        entry = d.entry
        for rel in d.modified:''',
     '''    out: list[str] = []
    for d in diff_all(manifest, checkout, roots, box_tags):
        if d.mismatch:
            continue
        reached, sub = only_scope(only, d.entry.repo)
        if not reached:
            continue
        d = scope_diff(d, sub)
        entry = d.entry
        for rel in d.modified:'''),
    ('''    if only:
        items = [i for i in items if i.entry.repo.startswith(only)]
    if base_override is not None:''',
     '''    if only:
        def _reached(i):
            ok, sub = only_scope(only, i.entry.repo)
            return ok and rel_in_scope(i.rel, sub)
        items = [i for i in items if _reached(i)]
    if base_override is not None:'''),
])

patch("cli.py", [
    ('''from .syncmap import (_normalize_eol, diff_all, entry_gate_reason, files_differ,
                      line_stats)''',
     '''from .syncmap import (_normalize_eol, diff_all, entry_gate_reason, files_differ,
                      line_stats, only_scope)'''),
    ('''    hidden = _gated_matches(manifest, box, lambda r: r.startswith(args.only))''',
     '''    hidden = _gated_matches(manifest, box, lambda r: only_scope(args.only, r)[0])'''),
    ('''            risky = merge.two_way_labels(manifest, checkout, roots)''',
     '''            risky = merge.two_way_labels(manifest, checkout, roots, box.tags,
                                         only=getattr(args, "only", None))'''),
    ('''            # Both verbs carry --only since 0.4.0; getattr stays as armor
            # against the original 0.3.x bug, where reaching for args.only
            # unconditionally crashed collect while apply passed.
            only = getattr(args, "only", None)
            if only:
                risky = [r for r in risky if r.startswith(only.split("/")[-1])]
            if risky:''',
     '''            # --only is applied inside two_way_labels, component-wise, sub-entry
            # included -- the old post-filter on the last path component
            # ("skills" for --only dotclaude/skills) could not express a
            # subtree and matched by accident on a shared last component.
            if risky:'''),
    ('''                for d in diff_all(manifest, checkout, roots, box.tags):
                    if d.mismatch or not d.modified:
                        continue
                    for rel in d.modified:
                        kind, evidence = _classify(checkout, d, rel)''',
     '''                for d in diff_all(manifest, checkout, roots, box.tags):
                    if d.mismatch or not d.modified:
                        continue
                    for rel in d.modified:
                        # direction skips are advisory per file; scoping them
                        # is not required for correctness, but skipping the
                        # attribution of files --only will not touch saves a
                        # history walk per file on a big payload.
                        reached, sub = only_scope(getattr(args, "only", None), d.entry.repo)
                        if not reached:
                            break
                        from .syncmap import rel_in_scope
                        if not rel_in_scope(rel, sub):
                            continue
                        kind, evidence = _classify(checkout, d, rel)'''),
    ('''            sp.add_argument("--only", default=None,
                            help="limit to entries whose repo path starts with this prefix")
            sp.add_argument("--add", action="store_true",''',
     '''            sp.add_argument("--only", default=None,
                            help="limit to one entry (dotclaude/skills), a parent of entries "
                                 "(dotclaude), or a subtree inside an entry "
                                 "(dotclaude/skills/test-mutation); whole path components only")
            sp.add_argument("--add", action="store_true",'''),
    ('''            sp.add_argument("--only", default=None,
                            help="limit to entries whose repo path starts with this prefix")
            sp.add_argument("--accept", action="store_true",''',
     '''            sp.add_argument("--only", default=None,
                            help="limit to one entry, a parent of entries, or a subtree / file "
                                 "inside an entry (dotclaude/skills/x/SKILL.md); whole path "
                                 "components only")
            sp.add_argument("--accept", action="store_true",'''),
    ('''            sp.add_argument("--only", default=None,
                            help="limit to entries whose repo path starts with this prefix")
            sp.add_argument("--sync-removals", action="store_true",''',
     '''            sp.add_argument("--only", default=None,
                            help="limit to one entry (dotclaude/skills), a parent of entries "
                                 "(dotclaude), or a subtree inside an entry "
                                 "(dotclaude/skills/test-mutation); whole path components only")
            sp.add_argument("--sync-removals", action="store_true",'''),
])
print("patched")
