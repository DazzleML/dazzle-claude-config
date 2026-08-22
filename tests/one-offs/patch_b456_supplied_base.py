"""One-off: apply the B4-B6 edits to merge.py (supplied base, conflict-on-
delete seeding, live-only accept). Reproducible from snapshot
`pre-B3-validator` + commit 10594a6; asserts on the old text, run once."""
from pathlib import Path

p = Path(__file__).resolve().parents[2] / "dazzle_claude_config" / "merge.py"
s = p.read_text(encoding="utf-8")


def swap(old: str, new: str) -> None:
    global s
    assert old in s, old[:90]
    s = s.replace(old, new, 1)


# 1. MergeItem fields
swap('''    reason: str | None = None  # set when the item is refused rather than merged
    sibling: tuple | None = None  # (path, sha, n, ratio) -- nearest historical
                                  # version, REJECTED as a base but worth a look
''', '''    reason: str | None = None  # set when the item is refused rather than merged
    sibling: tuple | None = None  # (path, sha, n, ratio) -- nearest historical
                                  # version, REJECTED as a base but worth a look
    # ADOPTION: the base came from outside the checkout (--base-file /
    # --base-from). A supplied base is a fact, not an estimate: the phantom
    # check does not run on it, the seed uses conflict-on-delete so nothing
    # the payload removed vanishes silently, and --accept writes LIVE ONLY
    # (see _write_back). `base_label` names the source for the record line.
    base_supplied: bool = False
    base_label: str = ""
    cod: object | None = None      # basefind.CodStats once seeded
''')

# 2. plan() / _head_items() take the override
swap('''def plan(manifest: Manifest, checkout: Path, roots: dict[str, Path], *,
         theirs_from: str = "head", stage: Path | None = None,
         base_mode: str = "auto") -> list[MergeItem]:''',
     '''def plan(manifest: Manifest, checkout: Path, roots: dict[str, Path], *,
         theirs_from: str = "head", stage: Path | None = None,
         base_mode: str = "auto", base_override: bytes | None = None,
         base_label: str = "") -> list[MergeItem]:''')
swap('''        items.extend(_head_items(manifest, checkout, roots, items, stage, base_mode))''',
     '''        items.extend(_head_items(manifest, checkout, roots, items, stage, base_mode,
                                 base_override=base_override, base_label=base_label))''')
swap('''def _head_items(manifest: Manifest, checkout: Path, roots: dict[str, Path],
                already: list[MergeItem], stage: Path | None,
                base_mode: str = "auto") -> list[MergeItem]:''',
     '''def _head_items(manifest: Manifest, checkout: Path, roots: dict[str, Path],
                already: list[MergeItem], stage: Path | None,
                base_mode: str = "auto", base_override: bytes | None = None,
                base_label: str = "") -> list[MergeItem]:''')
swap('''        rej: list = []
        found = infer_base(checkout, repo_path, live.read_bytes(), p.stdout,
                           rejected=rej)
        if found is not None:''',
     '''        rej: list = []
        if base_override is not None:
            # Supplied from outside the checkout. Not inferred, not phantom-
            # checked: the check is one-directional (it cannot see bases from
            # the box's own lineage) and rejects the correct recorded base
            # whenever a box deleted >= 3 shared lines. The loss table and
            # the reviewer are the judgement instead.
            base_f = stage / (theirs.stem + ".base-SUPPLIED")
            base_f.write_bytes(base_override)
            item.base = base_f
            item.base_supplied = True
            item.base_label = base_label
            found = None
        else:
            found = infer_base(checkout, repo_path, live.read_bytes(), p.stdout,
                               rejected=rej)
        if found is not None:''')

# 3. seed(): conflict-on-delete for a supplied base
swap('''def seed(item: MergeItem, merged: Path, union: bool = False) -> int:''',
     '''def seed(item: MergeItem, merged: Path, union: bool = False,
         cod_ratio: float | None = None) -> int:''')
swap('''    if item.base is None:
        if not union:
            merged.write_bytes(item.live.read_bytes())
            return 1 if _differs(item.live, item.repo) else 0''',
     '''    if item.base_supplied and not union:
        # CONFLICT-ON-DELETE. A correct three-way merge against a true
        # ancestor silently drops every region the payload removed while this
        # box kept it -- on a box with its own manual, exactly the lines
        # nobody wants gone. Strip those regions from the base, merge, then
        # wrap each one that landed in the clean output as a reviewer hunk.
        # Measured: 166 silent lines -> 24 hunks, 0 lost.
        from . import basefind
        ours_l = basefind.lines_of(item.live.read_bytes())
        base_l = basefind.lines_of(item.base.read_bytes())
        theirs_l = basefind.lines_of(item.repo.read_bytes())
        out, stats = basefind.conflict_on_delete(
            ours_l, base_l, theirs_l, merged.parent / (merged.name + ".inputs"),
            cod_ratio if cod_ratio is not None else basefind.DEFAULT_RATIO)
        merged.write_bytes("\\n".join(out).encode("utf-8"))
        item.cod = stats
        return stats.hunks
    if item.base is None:
        if not union:
            merged.write_bytes(item.live.read_bytes())
            return 1 if _differs(item.live, item.repo) else 0''')

# 4. run(): kwargs, the single-file rule, seeding, live-only accept
swap('''        preview: bool = False, base_mode: str = "auto",
        confirm_loss=None) -> MergeResult:''',
     '''        preview: bool = False, base_mode: str = "auto",
        confirm_loss=None, base_override: bytes | None = None,
        base_label: str = "", cod_ratio: float | None = None) -> MergeResult:''')
swap('''    items = plan(manifest, checkout, roots, stage=ws, base_mode=base_mode)
    if only:
        items = [i for i in items if i.entry.repo.startswith(only)]
''', '''    items = plan(manifest, checkout, roots, stage=ws, base_mode=base_mode,
                 base_override=base_override, base_label=base_label)
    if only:
        items = [i for i in items if i.entry.repo.startswith(only)]
    if base_override is not None:
        # One base is one file's ancestor. Applying it to several files would
        # hand every other file a wrong base with the phantom check switched
        # off -- refuse unless the run is scoped to exactly one.
        supplied = [i for i in items if i.base_supplied]
        if len(supplied) != 1:
            raise MergeError(
                f"--base-file/--base-from is one file's ancestor, but this run "
                f"covers {len(supplied)} merge candidates"
                + (": " + ", ".join(i.label for i in supplied[:6]) if supplied else "")
                + " -- scope it with --only <entry/path>")
''')
swap('''            seed(item, merged, union=union)   # absent, or untouched since seeding''',
     '''            seed(item, merged, union=union, cod_ratio=cod_ratio)   # absent, or untouched''')
swap('''        if accept:
            bdir = roots["USER_CLAUDE"] / "backups" / "ccs-merge"
            _write_back(item, merged, bdir)
            res.backup_dir = bdir
        res.resolved.append(item)''',
     '''        if accept:
            bdir = roots["USER_CLAUDE"] / "backups" / "ccs-merge"
            _write_back(item, merged, bdir, live_only=item.base_supplied)
            res.backup_dir = bdir
            if item.base_supplied:
                res.adopted.append(item)
        res.resolved.append(item)''')
swap('''    # Resolved items whose validation honoured deletions (see ValidationResult).
    honoured: list[tuple[MergeItem, ValidationResult]] = field(default_factory=list)
''', '''    # Resolved items whose validation honoured deletions (see ValidationResult).
    honoured: list[tuple[MergeItem, ValidationResult]] = field(default_factory=list)
    # Supplied-base items installed live-only (the checkout stays at HEAD).
    adopted: list[MergeItem] = field(default_factory=list)
''')

# 5. _write_back live-only
swap('''def _write_back(item: MergeItem, merged: Path,
                backup_dir: Path | None = None) -> None:
    """Install a VALIDATED merge on both sides. Never called before validate().
''', '''def _write_back(item: MergeItem, merged: Path,
                backup_dir: Path | None = None, live_only: bool = False) -> None:
    """Install a VALIDATED merge on both sides. Never called before validate().

    `live_only` is the ADOPTION case (a supplied base): the checkout stays at
    HEAD. Installing a keep box's merge into the checkout would publish its
    own sections to every other box and make them the next inferred base --
    the exact mechanism that deletes them on the following merge.
''')
swap('''    item.live.parent.mkdir(parents=True, exist_ok=True)
    dest_repo.parent.mkdir(parents=True, exist_ok=True)
    item.live.write_bytes(blob)
    dest_repo.write_bytes(blob)
''', '''    item.live.parent.mkdir(parents=True, exist_ok=True)
    item.live.write_bytes(blob)
    if live_only:
        return
    dest_repo.parent.mkdir(parents=True, exist_ok=True)
    dest_repo.write_bytes(blob)
''')

p.write_text(s, encoding="utf-8")
print("patched", p)
