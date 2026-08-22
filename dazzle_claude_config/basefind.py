"""Adoption merges: a base supplied from outside the checkout, and what the
three-way merge would silently let go of.

A box that forked its file before the payload existed has no ancestor in the
checkout's history. Its true ancestor may live one repository over (the home
repo of the machine that seeded it). Handed that base, `git merge-file`
produces a correct merge -- and a correct merge DROPS every region the
payload removed since the ancestor while the box left it untouched. Honoured
deletions, by three-way semantics; and on a box that keeps its own manual,
exactly the lines nobody wants to lose. Measured on the real file: 166 lines
in 38 headings gone, no marker, no word.

Two pieces, both pure functions over line lists so they can be tested without
git and shared by `merge` and its `--dry-run` table:

- **conflict-on-delete**: every base region that theirs removed while ours
  kept it verbatim is stripped from the base before the merge (so it reads
  as an ours-insertion and survives into the clean output) and then, where
  it landed in the clean output, re-wrapped as a reviewer hunk with the
  region on the ours and base panes and nothing on theirs. The reviewer
  decides; nothing is silent. 166 lines -> 24 hunks, 0 lost, on the real file.
- **the loss table**: per side, the lines unique to that side that are absent
  from the clean output and from every hunk ("silent"); of those, the ones
  in the base (honoured deletions) and the ones NOT in the base -- a side's
  own addition, gone. The last number must be 0; anything else is a tool bug.

Counting cannot tell a good base from a bad one -- `merge-file` never drops a
side's additions, so every silent loss is an honoured deletion, and whether
that is benign (the other box's OS section) or a catastrophe (this box's
service manual) is a matter of provenance and review. The table gives the
reviewer the facts; it does not decide for them.
"""
from __future__ import annotations

import difflib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .syncmap import _normalize_eol

#: Block-swap similarity. A `replace` opcode base->theirs is a REMOVAL (the
#: class above) rather than a rewrite when fewer than half its non-blank base
#: lines have a line at least this similar on the theirs side. A one-line
#: rewording scores ~0.8+; a retired section scores ~0. Plateau 0.45-0.70 on
#: the real file, cliff below 0.40.
DEFAULT_RATIO = 0.6

OURS_MARK = "<<<<<<< ours (kept verbatim; theirs deleted it since base)"
BASE_MARK = "||||||| base"
SEP_MARK = "======="
THEIRS_MARK = ">>>>>>> theirs"


def lines_of(blob: bytes) -> list[str]:
    """LF-normalised lines. utf-8-sig: a BOM from a Windows editor must not
    become part of the first line's identity (it made the loss table say
    "you removed `# Config`" on a file that had not changed -- tester run-01)."""
    return _normalize_eol(blob).decode("utf-8-sig", "replace").split("\n")


def is_block_swap(base_reg: list[str], theirs_reg: list[str],
                  ratio: float = DEFAULT_RATIO) -> bool:
    bl = [l for l in base_reg if l.strip()]
    tl = [l for l in theirs_reg if l.strip()]
    if not bl:
        return False
    hits = sum(1 for b in bl if any(
        difflib.SequenceMatcher(None, b, t, autojunk=False).ratio() >= ratio for t in tl))
    return hits < len(bl) / 2


@dataclass
class Region:
    b1: int          # base line range theirs removed
    b2: int
    ours: list[str]  # the same lines as ours holds them (verbatim-equal)


def removed_regions(base: list[str], ours: list[str], theirs: list[str],
                    ratio: float = DEFAULT_RATIO) -> list[Region]:
    """Base regions theirs deleted (or block-swapped) that ours left byte-equal."""
    b2o: dict[int, int] = {}
    for t, i1, i2, j1, j2 in difflib.SequenceMatcher(None, base, ours, autojunk=False).get_opcodes():
        if t == "equal":
            for k in range(i2 - i1):
                b2o[i1 + k] = j1 + k
    out: list[Region] = []
    for t, i1, i2, j1, j2 in difflib.SequenceMatcher(None, base, theirs, autojunk=False).get_opcodes():
        removal = t == "delete" or (t == "replace" and is_block_swap(base[i1:i2], theirs[j1:j2], ratio))
        if not removal:
            continue
        if all(i in b2o for i in range(i1, i2)) and any(base[i].strip() for i in range(i1, i2)):
            out.append(Region(i1, i2, [ours[b2o[i]] for i in range(i1, i2)]))
    return out


def strip_regions(base: list[str], regions: list[Region]) -> list[str]:
    drop = {i for r in regions for i in range(r.b1, r.b2)}
    return [l for i, l in enumerate(base) if i not in drop]


def _find(seq: list[str], sub: list[str], start: int = 0) -> int:
    n = len(sub)
    for k in range(start, len(seq) - n + 1):
        if seq[k:k + n] == sub:
            return k
    return -1


def _hunk_mask(merged: list[str]) -> list[bool]:
    mask = [False] * len(merged)
    inside = False
    for k, l in enumerate(merged):
        if l.startswith("<<<<<<<"):
            inside = True
        mask[k] = inside
        if l.startswith(">>>>>>>"):
            inside = False
    return mask


@dataclass
class CodStats:
    regions: int = 0          # removed regions found
    region_lines: int = 0     # base lines in them
    natural: int = 0          # conflicts merge-file produced on the stripped base
    in_hunk: int = 0          # regions that already fell inside a natural hunk
    wrapped: int = 0          # regions re-wrapped as new hunks
    repaired: int = 0         # natural hunks whose empty base pane got its region back
    missing: int = 0          # regions found verbatim nowhere in the output: a
                              # natural hunk swallowed part of them. Their lines
                              # are still in the file (inside that hunk), so
                              # nothing is silent -- the loss table proves it.

    @property
    def hunks(self) -> int:
        return self.natural + self.wrapped


def wrap_clean_regions(merged: list[str], regions: list[Region]) -> tuple[list[str], int, int, int]:
    """Wrap each region that landed verbatim in the CLEAN part of the merge
    output as a reviewer hunk. Returns (lines, wrapped, already_in_hunk, missing)."""
    mask = _hunk_mask(merged)
    todo = []
    missing = 0
    for r in regions:
        at = _find(merged, r.ours)
        if at < 0:
            missing += 1
            continue
        if any(mask[at:at + len(r.ours)]):
            todo.append((at, None))
            continue
        todo.append((at, r))
    in_hunk = sum(1 for _, r in todo if r is None)
    pos = {at: r for at, r in todo if r is not None}
    out: list[str] = []
    k = 0
    wrapped = 0
    while k < len(merged):
        r = pos.get(k)
        if r is not None:
            out += [OURS_MARK, *r.ours, BASE_MARK, *r.ours, SEP_MARK, THEIRS_MARK]
            wrapped += 1
            k += len(r.ours)
        else:
            out.append(merged[k])
            k += 1
    return out, wrapped, in_hunk, missing


def restore_base_panes(merged: list[str], regions: list[Region]) -> tuple[list[str], int]:
    """A removed region that fell inside a NATURAL hunk was stripped from the
    base before the merge, so that hunk's base pane is empty -- "nothing in
    base" -- when the real ancestor held the region. Put it back: the three
    panes must tell the truth, and a reviewer deciding between ours and theirs
    needs to see that theirs REPLACED this, not that both sides added here.
    Returns (lines, hunks repaired)."""
    out: list[str] = []
    repaired = 0
    i = 0
    while i < len(merged):
        l = merged[i]
        if not l.startswith("<<<<<<<"):
            out.append(l)
            i += 1
            continue
        # collect one hunk
        j = i + 1
        ours_p: list[str] = []
        base_p: list[str] = []
        theirs_p: list[str] = []
        pane = ours_p
        bmark = smark = None
        while j < len(merged) and not merged[j].startswith(">>>>>>>"):
            if merged[j].startswith("|||||||"):
                bmark = merged[j]; pane = base_p
            elif merged[j].startswith("======="):
                smark = merged[j]; pane = theirs_p
            else:
                pane.append(merged[j])
            j += 1
        end = merged[j] if j < len(merged) else THEIRS_MARK
        if bmark is not None and not any(x.strip() for x in base_p):
            found: list[str] = []
            for r in regions:
                if _find(ours_p, r.ours) >= 0:
                    found += r.ours
            if found:
                base_p = found
                repaired += 1
        out += [l, *ours_p, bmark or BASE_MARK, *base_p, smark or SEP_MARK, *theirs_p, end]
        i = j + 1
    return out, repaired


def merge_file_diff3(ours: list[str], base: list[str], theirs: list[str],
                     workdir: Path) -> tuple[list[str], int]:
    """`git merge-file -p --diff3` over three line lists. Returns (lines, rc)
    where rc is the conflict count (or 255 on error -- callers must check)."""
    workdir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, ls in (("o", ours), ("b", base), ("t", theirs)):
        p = workdir / name
        p.write_bytes("\n".join(ls).encode("utf-8"))
        paths.append(str(p))
    r = subprocess.run(["git", "merge-file", "-p", "--diff3",
                        "-L", "ours", "-L", "base", "-L", "theirs", *paths],
                       capture_output=True)
    return r.stdout.decode("utf-8", "replace").split("\n"), r.returncode


def conflict_on_delete(ours: list[str], base: list[str], theirs: list[str],
                       workdir: Path, ratio: float = DEFAULT_RATIO) -> tuple[list[str], CodStats]:
    """The whole move: strip, merge, wrap. Output lines + the counts."""
    regs = removed_regions(base, ours, theirs, ratio)
    stats = CodStats(regions=len(regs), region_lines=sum(r.b2 - r.b1 for r in regs))
    merged, rc = merge_file_diff3(ours, strip_regions(base, regs), theirs, workdir)
    if rc == 255:
        raise RuntimeError("git merge-file failed")
    stats.natural = rc
    out, stats.wrapped, stats.in_hunk, stats.missing = wrap_clean_regions(merged, regs)
    out, stats.repaired = restore_base_panes(out, regs)
    return out, stats


# --- the loss table ----------------------------------------------------------

def split_hunks(merged: list[str]) -> tuple[list[str], set[str]]:
    """(clean lines, set of stripped lines that appear inside any hunk)."""
    clean: list[str] = []
    hunk: set[str] = set()
    inside = False
    for l in merged:
        if l.startswith("<<<<<<<"):
            inside = True
            continue
        if l.startswith("|||||||") or l.startswith("======="):
            continue
        if l.startswith(">>>>>>>"):
            inside = False
            continue
        (hunk.add(l.strip()) if inside else clean.append(l))
    return clean, hunk


@dataclass
class SideLoss:
    unique: int = 0      # non-blank lines of this side absent from the other
    silent: int = 0      # of those, absent from the clean output AND every hunk
    honoured: int = 0    # of the silent, in the base: the other side deleted them
    lost: int = 0        # of the silent, NOT in the base: this side's addition, gone
    first_honoured: str = ""
    first_lost: str = ""


def side_loss(x: list[str], y: list[str], base: list[str] | None,
              clean: list[str], hunk: set[str]) -> SideLoss:
    yset = {l.strip() for l in y}
    bset = {l.strip() for l in base} if base is not None else set()
    uniq = [l for l in x if l.strip() and l.strip() not in yset]
    sm = difflib.SequenceMatcher(None, x, clean, autojunk=False)
    gone = {l.strip() for t, i1, i2, j1, j2 in sm.get_opcodes()
            if t in ("delete", "replace") for l in x[i1:i2]}
    silent = [l for l in uniq if l.strip() in gone and l.strip() not in hunk]
    res = SideLoss(unique=len(uniq), silent=len(silent))
    for l in silent:
        if l.strip() in bset:
            res.honoured += 1
            res.first_honoured = res.first_honoured or l.strip()
        else:
            res.lost += 1
            res.first_lost = res.first_lost or l.strip()
    return res


@dataclass
class LossTable:
    ours: SideLoss = field(default_factory=SideLoss)
    theirs: SideLoss = field(default_factory=SideLoss)
    hunks: int = 0

    @property
    def lost(self) -> int:
        return self.ours.lost + self.theirs.lost


def loss_table(ours: list[str], theirs: list[str], base: list[str] | None,
               merged: list[str]) -> LossTable:
    clean, hunk = split_hunks(merged)
    return LossTable(ours=side_loss(ours, theirs, base, clean, hunk),
                     theirs=side_loss(theirs, ours, base, clean, hunk),
                     hunks=sum(1 for l in merged if l.startswith("<<<<<<<")))


def distance(a: list[str], b: list[str]) -> int:
    """Line-edit distance (changed lines), the d(ours)/d(theirs) of the table."""
    n = 0
    for t, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if t != "equal":
            n += (i2 - i1) + (j2 - j1)
    return n


def read_base_from(spec: str) -> tuple[bytes, str]:
    """`REPO[@SHA]:PATH` -> (blob, label). SHA defaults to HEAD. PATH is
    required (a repository has no single file). The path is whatever follows
    the LAST colon, so `C:\repo:.claude/CLAUDE.md` parses on Windows."""
    head, sep, path = spec.rpartition(":")
    if not sep or not path or (len(head) == 1 and head.isalpha()):
        raise ValueError("--base-from needs REPO[@SHA]:PATH (the path inside the repo)")
    repo, at, sha = head.rpartition("@")
    if not at:
        repo, sha = head, "HEAD"
    sha = sha or "HEAD"
    r = subprocess.run(["git", "-C", repo, "show", f"{sha}:{path}"], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise ValueError(f"--base-from: git show {sha}:{path} in {repo} failed: "
                         f"{r.stderr.decode('utf-8', 'replace').strip()[:120]}")
    short = subprocess.run(["git", "-C", repo, "rev-parse", "--short", sha],
                           capture_output=True, text=True).stdout.strip() or sha
    return r.stdout, f"{short}:{path}"
