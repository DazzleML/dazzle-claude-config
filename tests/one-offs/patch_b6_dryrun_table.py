"""One-off: fold the `ccs base` verb into `ccs merge --dry-run` (maintainer
decision 2026-08-22: a bare `base` verb reads weak beside status/merge/
collect/apply; `--base <path>` would collide with merge's existing --base;
the pre-merge look IS the dry run of the merge). Asserts on the old text."""
from pathlib import Path

p = Path(__file__).resolve().parents[2] / "dazzle_claude_config" / "cli.py"
s = p.read_text(encoding="utf-8")


def swap(old: str, new: str) -> None:
    global s
    assert old in s, old[:90]
    s = s.replace(old, new, 1)


# 1. verb list: back to the original order, no `base`
swap('''    for verb, doc in (("collect", "copy live config INTO the checkout (guarded)"),
                      ("apply", "copy checkout config INTO the live tree (backed up)"),
                      ("merge", "resolve files that differ on BOTH sides, in your diff tool"),
                      ("base", "  with merge: which ancestor it would use, and what that "
                               "ancestor would let go of -- run it first (read-only)"),
                      ("status", "three-way drift report (exit 1 when drift)"),
                      ("diff", "list per-file differences")):''',
     '''    for verb, doc in (("collect", "copy live config INTO the checkout (guarded)"),
                      ("apply", "copy checkout config INTO the live tree (backed up)"),
                      ("merge", "resolve files that differ on BOTH sides, in your diff tool"),
                      ("status", "three-way drift report (exit 1 when drift)"),
                      ("diff", "list per-file differences")):''')

# 2. options: drop the base verb from the option sets; --dry-run help on merge
swap('''        if verb in ("merge", "diff", "base"):
            sp.add_argument("--base-file", default=None, metavar="FILE",''',
     '''        if verb in ("merge", "diff"):
            sp.add_argument("--base-file", default=None, metavar="FILE",''')
swap('''        if verb in ("merge", "base"):
            sp.add_argument("--block-swap-ratio", default=None, type=float,''',
     '''        if verb == "merge":
            sp.add_argument("--block-swap-ratio", default=None, type=float,''')
swap('''        if verb == "base":
            sp.add_argument("path", help="the file, as `ccs diff <path>` names it")
''', '')
swap('''        if verb in ("collect", "apply", "merge"):
            sp.add_argument("--dry-run", action="store_true")''',
     '''        if verb in ("collect", "apply"):
            sp.add_argument("--dry-run", action="store_true")
        if verb == "merge":
            sp.add_argument("--dry-run", action="store_true",
                            help="list what would merge and, per file, which ancestor "
                                 "it would use and what that ancestor would let go of "
                                 "(the loss table; `lost` must be 0). Nothing is written")''')

# 3. _run_base -> _print_base_table(item, ...) for the dry-run path
start = s.index("def _run_base(args, manifest, checkout, roots, repo) -> int:")
end = s.index("def _print_honoured(v) -> None:")
new_fn = '''def _print_base_table(item, checkout, roots, supplied: tuple, ratio) -> None:
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


'''
s = s[:start] + new_fn + s[end:]

# 4. dispatch: remove the verb; print the table under "would merge"
swap('''        if args.verb == "base":
            return _run_base(args, manifest, checkout, roots, repo)

''', '')
swap('''            for item in r.planned:
                print(f"{c('magenta', 'would merge')}: {item.label}")''',
     '''            for item in r.planned:
                print(f"{c('magenta', 'would merge')}: {item.label}")
                if repo is not None:
                    _print_base_table(item, checkout, roots, (blob, label),
                                      args.block_swap_ratio)''')

p.write_text(s, encoding="utf-8")
print("patched")
