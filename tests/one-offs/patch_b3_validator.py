"""One-off: apply the B3 edits to merge.py (honoured deletions, loss prefix,
the no-base ask). Kept so the change is reproducible from the snapshot
`pre-B3-validator`; safe to re-run only once (asserts on the old text)."""
from pathlib import Path

p = Path(__file__).resolve().parents[2] / "dazzle_claude_config" / "merge.py"
s = p.read_text(encoding="utf-8")


def swap(old: str, new: str) -> None:
    global s
    assert old in s, old[:80]
    s = s.replace(old, new, 1)


swap('''class ValidationResult:
    """Why a merged file was accepted or rejected. Empty failures == accepted."""
    failures: list[str] = field(default_factory=list)
    survived: dict[str, bool] = field(default_factory=dict)
''', '''class ValidationResult:
    """Why a merged file was accepted or rejected. Empty failures == accepted."""
    failures: list[str] = field(default_factory=list)
    survived: dict[str, bool] = field(default_factory=dict)
    # Per side ("ours" / "theirs"): lines unique to that side that are absent
    # from the result. `honoured` = in the base too, so the OTHER side deleted
    # them on purpose since the common ancestor (a three-way merge is right to
    # drop them); kept as contiguous regions for the accept print, which is
    # also the tripwire for a wrong base. `lost` = NOT in the base -- that
    # side's own addition, gone -- which is what fails validation.
    honoured: dict[str, list[list[str]]] = field(default_factory=dict)
    lost: dict[str, list[str]] = field(default_factory=dict)

    @property
    def only_loss(self) -> bool:
        """True when every failure is a dropped-line failure -- the one kind a
        human who reviewed the file may knowingly accept without a base."""
        return bool(self.failures) and all(f.startswith(_LOSS_PREFIX)
                                           for f in self.failures)
''')

swap("_SUPERSEDE_RATIO = 0.5\n", "_SUPERSEDE_RATIO = 0.5\n_LOSS_PREFIX = \"dropped:\"\n")

swap('''        src_lines = _normalize_eol(src.read_bytes()).decode("utf-8", "replace").splitlines()
        res_lines = text.splitlines()
        dropped: list[str] = []
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                None, src_lines, res_lines, autojunk=False).get_opcodes():
            if tag == "delete":
                dropped += [l for l in src_lines[i1:i2] if l.strip()]
            elif tag == "replace":''',
     '''        src_lines = _normalize_eol(src.read_bytes()).decode("utf-8", "replace").splitlines()
        res_lines = text.splitlines()
        dropped: list[str] = []
        regions: list[list[str]] = []   # the same lines, grouped by opcode region
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                None, src_lines, res_lines, autojunk=False).get_opcodes():
            if tag == "delete":
                reg = [l for l in src_lines[i1:i2] if l.strip()]
                dropped += reg
                regions.append(reg)
            elif tag == "replace":''')

swap('''                gone, came = src_lines[i1:i2], res_lines[j1:j2]
                for old in gone:
                    if not old.strip():
                        continue
                    best = max((difflib.SequenceMatcher(None, old, new).ratio()
                                for new in came), default=0.0)
                    if best < _SUPERSEDE_RATIO:
                        dropped.append(old)
        # Only content the OTHER side never had can have been lost here;
        # anything both sides share cannot go missing by choosing a side.
        other_lines = _lines(other)
        dropped = [l for l in dropped if l.strip() not in other_lines]
        # Dropping a regressed pattern is the POINT, not a loss. The hints tell
        # the user to prefer the side without it; flagging its absence as
        # missing content would have the tool contradicting its own advice.
        dropped = [l for l in dropped
                   if not any(pat in l for pat in regressed)]
        if dropped:
            res.failures.append(
                f"{len(dropped)} line(s) present only in {side} were dropped "
                f"outright -- not replaced (first: {sorted(dropped)[0][:70]!r})")
''', '''                gone, came = src_lines[i1:i2], res_lines[j1:j2]
                reg = []
                for old in gone:
                    if not old.strip():
                        continue
                    best = max((difflib.SequenceMatcher(None, old, new).ratio()
                                for new in came), default=0.0)
                    if best < _SUPERSEDE_RATIO:
                        dropped.append(old)
                        reg.append(old)
                if reg:
                    regions.append(reg)
        # Only content the OTHER side never had can have been lost here;
        # anything both sides share cannot go missing by choosing a side.
        other_lines = _lines(other)

        # Dropping a regressed pattern is the POINT, not a loss. The hints tell
        # the user to prefer the side without it; flagging its absence as
        # missing content would have the tool contradicting its own advice.
        def _counts(l: str) -> bool:
            return (l.strip() not in other_lines
                    and not any(pat in l for pat in regressed))
        dropped = [l for l in dropped if _counts(l)]
        # HONOURED DELETION. With a base, a line that is in the base and absent
        # from the other side was deleted by that side on purpose, and a
        # three-way merge is RIGHT to drop it -- an upstream retirement of a
        # rule, a box removing a section it never wanted. Until this rule the
        # gate refused every such result, so nothing retired upstream could
        # ever land through a two-sided file (#16). What still fails is a line
        # NOT in the base: that side's own addition, gone for no reason git
        # could have had.
        base_lines = _lines(item.base) if item.base and item.base.is_file() else set()
        if base_lines:
            honoured_regions = [[l for l in reg if _counts(l) and l.strip() in base_lines]
                                for reg in regions]
            honoured_regions = [r for r in honoured_regions if r]
            if honoured_regions:
                res.honoured[side] = honoured_regions
            dropped = [l for l in dropped if l.strip() not in base_lines]
        if dropped:
            res.lost[side] = dropped
            res.failures.append(
                f"{_LOSS_PREFIX} {len(dropped)} line(s) present only in {side} were "
                f"dropped outright -- not replaced (first: {sorted(dropped)[0][:70]!r})")
''')

swap('''    no_base: list[MergeItem] = field(default_factory=list)
    siblings: list[MergeItem] = field(default_factory=list)
''', '''    no_base: list[MergeItem] = field(default_factory=list)
    siblings: list[MergeItem] = field(default_factory=list)
    # Items a human accepted knowing lines would be dropped (no base, so the
    # tool could not tell a deliberate deletion from an accident; the person
    # could). Also in `resolved`; listed here so the CLI can say so.
    accepted_with_loss: list[tuple[MergeItem, ValidationResult]] = field(default_factory=list)
''')

swap('''        union: bool = False, launch_tool: bool = True,
        preview: bool = False, base_mode: str = "auto") -> MergeResult:
    """Plan, seed, validate and (optionally) hand off each divergent file.
''', '''        union: bool = False, launch_tool: bool = True,
        preview: bool = False, base_mode: str = "auto",
        confirm_loss=None) -> MergeResult:
    """Plan, seed, validate and (optionally) hand off each divergent file.

    `confirm_loss(item, validation) -> bool` is asked, once per file, when a
    result with NO base fails only because lines unique to one side were
    dropped and a human resolved the file in a tool. Without a base the tool
    cannot tell a deliberate deletion from an accident -- the person who just
    reviewed the file can -- so the default shows the lines and asks on a
    console, and refuses anywhere a console is absent.
''')

swap('''        v = validate(item, merged, probes=probes)
        if not v.ok:
            res.unresolved.append((item, v))
            continue
''', '''        v = validate(item, merged, probes=probes)
        if (not v.ok and item.base is None and v.only_loss and launch_tool
                and not union):
            ask = confirm_loss or _ask_loss_on_console
            if ask(item, v):
                res.accepted_with_loss.append((item, v))
                v = ValidationResult(honoured=v.honoured, lost=v.lost)
        if not v.ok:
            res.unresolved.append((item, v))
            continue
''')

swap('''def _dominant_eol(blob: bytes) -> bytes:''',
     '''def _ask_loss_on_console(item: MergeItem, v: ValidationResult) -> bool:
    """Default `confirm_loss`: print what would vanish, ask, default no.
    Non-interactive (CI, piped stdin) never accepts -- a silent yes here is
    exactly the class of failure the gate exists to stop."""
    if not interactive():
        return False
    print(f"no base for {item.label}: ccs cannot tell whether these lines were "
          f"deleted on purpose or by accident --")
    for side, lines in v.lost.items():
        print(f"  only in {side}, absent from your result ({len(lines)}):")
        for ln in lines[:12]:
            print(f"    {ln[:100]}")
        if len(lines) > 12:
            print(f"    ... and {len(lines) - 12} more")
    try:
        answer = input("  you reviewed this file -- install it anyway? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in ("y", "yes")


def _dominant_eol(blob: bytes) -> bytes:''')

p.write_text(s, encoding="utf-8")
print("patched", p)
