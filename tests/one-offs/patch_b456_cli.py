"""One-off: B4-B6 CLI wiring -- --base-file/--base-from/--block-swap-ratio on
merge, --base-file on diff --difftool 3, adoption output, and the `ccs base`
verb. Asserts on the old text; run once."""
from pathlib import Path

p = Path(__file__).resolve().parents[2] / "dazzle_claude_config" / "cli.py"
s = p.read_text(encoding="utf-8")


def swap(old: str, new: str) -> None:
    global s
    assert old in s, old[:90]
    s = s.replace(old, new, 1)


# --- parser -------------------------------------------------------------
swap('''                      ("status", "three-way drift report (exit 1 when drift)"),
                      ("diff", "list per-file differences")):''',
     '''                      ("status", "three-way drift report (exit 1 when drift)"),
                      ("diff", "list per-file differences"),
                      ("base", "show which ancestor a merge would use, and what "
                               "it would silently let go of (read-only)")):''')

swap('''            sp.add_argument("--no-launch", action="store_true",
                            help="produce and validate the merged file without "
                                 "opening a diff tool")''',
     '''            sp.add_argument("--no-launch", action="store_true",
                            help="produce and validate the merged file without "
                                 "opening a diff tool")
        if verb in ("merge", "diff", "base"):
            sp.add_argument("--base-file", default=None, metavar="FILE",
                            help="use FILE as the common ancestor instead of "
                                 "inferring one from the checkout's history -- for "
                                 "ADOPTING a box whose file forked before the payload "
                                 "existed. One file only: scope merge with --only")
            sp.add_argument("--base-from", default=None, metavar="REPO[@SHA]:PATH",
                            help="like --base-file, but read the ancestor out of "
                                 "another git repository (SHA defaults to HEAD)")
        if verb in ("merge", "base"):
            sp.add_argument("--block-swap-ratio", default=None, type=float,
                            metavar="R",
                            help="with a supplied base: a region the payload "
                                 "REPLACED counts as a removal (and becomes a "
                                 "reviewer hunk) when fewer than half its lines have "
                                 "an R-similar line in the replacement (default 0.6; "
                                 "plateau 0.45-0.70)")
        if verb == "base":
            sp.add_argument("path", help="the file, as `ccs diff <path>` names it")''')

# --- helper: resolve the supplied base ----------------------------------
swap('''def _print_honoured(v) -> None:''',
     '''def _supplied_base(args) -> tuple[bytes | None, str]:
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


def _run_base(args, manifest, checkout, roots, repo) -> int:
    """`ccs base <path>`: the table that is the oracle for an adoption merge.

    One row per candidate base -- the inferred one (with the phantom verdict
    the guard would give it) and the supplied one (phantom-exempt) -- each
    with the conflict-on-delete merge's hunk count and the per-side loss
    numbers. `lost` is the one that must be 0: it counts a side's own
    additions missing from both the clean output and every hunk, which no
    base can legitimately cause. Same code path as `merge` seeds from, so
    the table and the merge cannot disagree.
    """
    from . import basefind
    import subprocess as sp
    from .manifest import Entry
    want = args.path.replace(chr(92), "/").strip("/")
    all_diffs = diff_all(manifest, checkout, roots)
    try:
        found = _resolve_pair(all_diffs, want)
    except AmbiguousPath as e:
        _print_ambiguous(e)
        return EXIT_ERROR
    if found is None:
        print(c("red", f"no such differing file: {args.path!r}"), file=sys.stderr)
        return EXIT_ERROR
    lv, rp, target, repo_label = found
    if repo is None or not lv.is_file():
        print(c("red", "base needs a git checkout and a live file"), file=sys.stderr)
        return EXIT_ERROR
    shown = sp.run(["git", "show", f"HEAD:{repo_label}"], cwd=str(checkout), capture_output=True)
    if shown.returncode != 0 or not shown.stdout:
        print(c("red", f"{repo_label} was never committed -- no history to attribute against"),
              file=sys.stderr)
        return EXIT_ERROR
    ours_b, theirs_b = lv.read_bytes(), shown.stdout
    ours_l, theirs_l = basefind.lines_of(ours_b), basefind.lines_of(theirs_b)
    ws = merge.workspace_for(roots) / "base"
    ws.mkdir(parents=True, exist_ok=True)
    ratio = args.block_swap_ratio if args.block_swap_ratio is not None else basefind.DEFAULT_RATIO

    print(c("bold", f"base candidates for {target}") + c("dim", f"  (checkout: {repo_label})"))
    print(c("dim", _LOSS_HEAD))
    rows = 0
    usable = None
    # 1. supplied
    blob, label = _supplied_base(args)
    if blob is not None:
        base_l = basefind.lines_of(blob)
        out, stats = basefind.conflict_on_delete(ours_l, base_l, theirs_l, ws / "supplied", ratio)
        table = basefind.loss_table(ours_l, theirs_l, base_l, out)
        verdict = "USABLE  conflict-on-delete on" if table.lost == 0 else "TOOL BUG  lost != 0"
        rows += 1
        print(_loss_row(rows, "supplied", label[:28], basefind.distance(base_l, ours_l),
                        basefind.distance(base_l, theirs_l), "exempt", table, verdict))
        usable = usable or (table.lost == 0 and (label, table, stats))
    # 2. inferred
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
    print()
    if usable:
        label, table, stats = usable
        o, t = table.ours, table.theirs
        line = (f"base: use {label} -- {table.hunks} hunk(s) to review; {table.lost} line(s) lost; "
                f"{o.honoured} line(s) of yours retired upstream (theirs wins)")
        if o.first_honoured:
            line += f": first {o.first_honoured[:50]!r}"
        print(c("green", line))
        if t.honoured:
            print(c("dim", f"      {t.honoured} theirs line(s) stay deleted (you removed them since base)"
                           + (f": first {t.first_honoured[:50]!r}" if t.first_honoured else "")))
        if stats is not None:
            print(c("dim", f"      conflict-on-delete: {stats.regions} removed region(s), "
                           f"{stats.region_lines} base line(s); {stats.natural} natural + "
                           f"{stats.wrapped} wrapped hunk(s) ({stats.in_hunk} inside natural hunks)"))
        return EXIT_CLEAN
    print(c("yellow", "base: none usable -- supply one with --base-file / --base-from, "
                      "or merge two-way (no base)"))
    return EXIT_DRIFT


def _print_honoured(v) -> None:''')

# --- merge verb: pass the supplied base through; print adoption lines ----
swap('''            r = merge.run(manifest, checkout, roots, tool=args.tool,
                          dry_run=args.dry_run, accept=args.accept, only=args.only,
                          union=args.union, launch_tool=not args.no_launch,
                          preview=args.preview, base_mode=args.base)''',
     '''            blob, label = _supplied_base(args)
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
                                 f"removed since the base)"))''')
swap('''            for item in r.resolved:
                verb = "merged and installed" if args.accept else "merged (not installed)"
                print(f"{c('green', verb)}: {item.label}")''',
     '''            adopted = {id(i) for i in r.adopted}
            for item in r.resolved:
                verb = "merged and installed" if args.accept else "merged (not installed)"
                if id(item) in adopted:
                    verb = "merged and installed LIVE ONLY"
                print(f"{c('green', verb)}: {item.label}")
                if id(item) in adopted:
                    print(c("yellow", f"    adoption merge: checkout left at HEAD; record "
                                      f"{item.base_label} as this box's base for {item.label}"))''')

# --- diff --difftool 3 --base-file ----------------------------------------
swap('''def _launch_three_way(lv, rp, target, repo_label, tool, checkout, roots, repo) -> int:''',
     '''def _launch_three_way(lv, rp, target, repo_label, tool, checkout, roots, repo,
                      supplied=None) -> int:''')
swap('''    shown = subprocess.run(["git", "show", f"HEAD:{repo_label}"], cwd=str(checkout),
                           capture_output=True)
    theirs = shown.stdout if shown.returncode == 0 else b""
    found = merge.infer_base(checkout, repo_label, lv.read_bytes(), theirs) if theirs else None
    if found is None:''',
     '''    shown = subprocess.run(["git", "show", f"HEAD:{repo_label}"], cwd=str(checkout),
                           capture_output=True)
    theirs = shown.stdout if shown.returncode == 0 else b""
    if supplied is not None and supplied[0] is not None:
        found = (supplied[0], "supplied")          # a fact, not an estimate
    else:
        found = merge.infer_base(checkout, repo_label, lv.read_bytes(), theirs) if theirs else None
    if found is None:''')
swap('''    if ways == 3:
        return _launch_three_way(lv, rp, target, repo_label, tool, checkout, roots, repo)''',
     '''    if ways == 3:
        return _launch_three_way(lv, rp, target, repo_label, tool, checkout, roots, repo,
                                 supplied=supplied)''')
swap('''def _launch_file_difftool(all_diffs, wanted: str, tool: str | None, *,''',
     '''def _launch_file_difftool(all_diffs, wanted: str, tool: str | None, *, supplied=None,''')
swap('''                    return _launch_file_difftool(all_diffs, wanted,
                                                 getattr(args, 'tool', None),
                                                 ways=ways, checkout=checkout,
                                                 roots=roots, repo=repo)''',
     '''                    return _launch_file_difftool(all_diffs, wanted,
                                                 getattr(args, 'tool', None),
                                                 ways=ways, checkout=checkout,
                                                 roots=roots, repo=repo,
                                                 supplied=_supplied_base(args))''')

# --- dispatch the base verb (before status/diff) -------------------------
swap('''        # status / diff
        all_diffs = diff_all(manifest, checkout, roots, box.tags)''',
     '''        if args.verb == "base":
            return _run_base(args, manifest, checkout, roots, repo)

        # status / diff
        all_diffs = diff_all(manifest, checkout, roots, box.tags)''')

p.write_text(s, encoding="utf-8")
print("patched", p)
