"""Three-way merge orchestration -- ccs drives git and the user's diff tool.

**The three-way itself is git's.** ``git merge-file`` computes the 3-way;
``git mergetool`` already drives ~20 tools through the ``$LOCAL $BASE $REMOTE
$MERGED`` contract, and ccs reimplements neither. What ccs adds sits above
that engine: which manifest entries may be merged at all; whether the result
actually kept both sides' content; since 0.5.16, what each tool does with a
$MERGED that already holds a person's work (the capability registry below),
which decides whether a resumed file may be reopened; and, when ``--ai``
lands (#19), a resolution workflow for the hunks that are not a side-pick --
deterministic classification first, a model on the residue, the same
validation gate on the result. That workflow is a merge *process*, not a
second diff3; the invariant below binds it exactly as it binds a hand edit.

THE INVARIANT (measured three times, three different costumes -- see DWP-5):

    Success means VALIDATED CONTENT SURVIVAL, never "the tool returned 0."

    - ``git merge-file`` with base==theirs   -> exit 0, 56 lines silently dropped
    - ``git merge-file`` with base==ours     -> exit 0, all 7 CLAUDE_USER_DIR refs deleted
    - upstream deletes an untouched file     -> exit 0, a depended-on skill vanishes

Every gate below exists because of one of those. If a future change short-circuits
validation because "git said it was clean", it reintroduces the whole class.

Phase 1 (this module) is the hand-off: plan, seed, validate, launch. It needs no
merge base -- with no base it degrades to an honest 2-way and says so. Phase 2
(machine branches) makes the base exact; nothing here has to change for it.
"""
from __future__ import annotations

import os
import difflib
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import inject
from .manifest import Entry, Manifest
from .platform_info import user_claude_dir
from .secrets import is_denied, scan_file
from .userconfig import not_valid_json
from . import render, seeddecisions
from .syncmap import EntryDiff, _normalize_eol, diff_all, only_scope, rel_in_scope, scope_diff

# Strategies whose target is a straight copy of one repo file. Anything else
# COMPOSES its target (see MERGE_REFUSED_STRATEGIES) and must not be merged at
# the target -- doing so bakes machine-specific values into the shared base.
MERGEABLE_STRATEGIES = {"copy", "seed-if-absent"}

# `render` composes base + os overlay + machine overlay + vars; `plugins`
# composes similarly. Merging the RENDERED target back into settings.base.json
# writes this machine's overlay values into the file everyone shares -- and
# then pushes them, possibly to a public repo via a contributor PR (AC-22/23).
MERGE_REFUSED_STRATEGIES = {"render", "plugins"}

# `|||||||` is the --diff3 BASE separator. Omitting it caused two failures at
# once: the marker went undetected AND its line was reported as "invented
# content" because it appears in none of the three inputs.
CONFLICT_MARKERS = ("<<<<<<<", "|||||||", "=======", ">>>>>>>")

# Content that was fixed locally and must never return via an upstream merge.
# Extended by --regressed-pattern; the default is the one measured this session.
DEFAULT_REGRESSED_PATTERNS = ("/home/dev/claude/",)

# How similar a replacement must be to count as a REWRITE of the line it
# replaced rather than unrelated text that clobbered it. Tuned against the
# real case: "Two of these are re-entrant..." vs "Three of these are
# re-entrant..." scores far above this; "THEIRS-SECTION-A" vs "OURS-ONLY"
# scores far below.
_SUPERSEDE_RATIO = 0.5
_LOSS_PREFIX = "dropped:"


class MergeError(RuntimeError):
    """Merge could not proceed (bad tool, unreadable input, refused entry)."""


# Exit codes -- distinct so scripts can branch on the reason (AC-6).
EXIT_OK = 0
EXIT_CONFLICT = 1
EXIT_NO_BASE = 2
EXIT_NO_TOOL = 3
EXIT_VALIDATION = 4
EXIT_REFUSED = 5


@dataclass
class MergeItem:
    """One file needing a decision, with the three inputs git will be given."""
    entry: Entry
    rel: str            # path relative to the entry target ("" for single-file)
    live: Path          # ours
    repo: Path          # theirs
    base: Path | None = None   # None => no ancestry; honest 2-way
    repo_dest: Path | None = None  # WHERE the checkout copy is installed.
                                   # Distinct from `repo`, which is only the
                                   # CONTENT of theirs -- on the HEAD axis that
                                   # is a staging file, and writing the merge
                                   # there installed nothing and clobbered the
                                   # copy of theirs.
    reason: str | None = None  # set when the item is refused rather than merged
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

    @property
    def mergeable(self) -> bool:
        return self.reason is None

    @property
    def label(self) -> str:
        t = self.entry.target or self.entry.repo
        return f"{t}/{self.rel}" if self.rel else t


@dataclass
class ValidationResult:
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

    @property
    def ok(self) -> bool:
        return not self.failures


def plan(manifest: Manifest, checkout: Path, roots: dict[str, Path], *,
         theirs_from: str = "head", stage: Path | None = None,
         base_mode: str = "auto", base_override: bytes | None = None,
         base_label: str = "") -> list[MergeItem]:
    """Enumerate what needs merging, refusing what must not be merged.

    Covers BOTH territories (dotclaude -> ~/.claude, userclaude -> ~/claude)
    because it walks the manifest rather than a hardcoded root (AC-20). An
    entry that only diverges under `userclaude` must not be silently skipped.

    `theirs_from` picks the divergence AXIS, and the default is the one the
    feature exists for:

      "head"     ours = live, theirs = the checkout's committed blob.
                 This is the incoming-upstream case. It is the ONLY axis that
                 sees content which exists solely in git -- after `ccs collect`
                 the checkout's working tree has been overwritten from live, so
                 upstream's version survives only at HEAD. Comparing worktrees
                 there reports "no difference" while a whole upstream revision
                 sits unmerged one commit away.

      "worktree" ours = live, theirs = the checkout's working file. Useful when
                 the checkout was edited directly, but blind to anything that
                 only exists in a commit.
    """
    # ORDER MATTERS. The HEAD axis is the richer one -- it is the only path
    # that infers a merge base -- so it must claim a label before the worktree
    # axis does. Running diff_all first let the worktree item win for any file
    # whose worktree happened to match HEAD, silently producing a baseless
    # 2-way for a file that had a perfectly good ancestor available.
    items: list[MergeItem] = []
    if theirs_from == "head":
        items.extend(_head_items(manifest, checkout, roots, items, stage, base_mode,
                                 base_override=base_override, base_label=base_label))
    seen = {i.label for i in items}
    for d in diff_all(manifest, checkout, roots):
        items.extend(i for i in _items_for_diff(d) if i.label not in seen)
    return items


def _head_candidates(manifest: Manifest, checkout: Path, roots: dict[str, Path]):
    """(entry, rel, live) tuples the HEAD axis must consider.

    Single-file entries yield themselves (rel "", any strategy -- the
    render/plugins refusals are produced downstream and must stay visible).
    Directory-target entries yield one tuple per file in diff_all's `modified`
    list. Until 0.4.x this axis checked is_file() on the entry target and
    silently skipped every directory entry, so neither the two-way guard nor
    base inference ever ran for the dominant real payload shape (TW1,
    checklist runs 03/04).
    """
    for entry in manifest.entries:
        if entry.territory is None or entry.target is None:
            continue
        live = roots[manifest.territories[entry.territory]["root_var"]] / entry.target
        if live.is_file():
            yield entry, "", live
    for d in diff_all(manifest, checkout, roots):
        if d.mismatch or d.live_base.is_file():
            continue                      # single-file shapes handled above
        for rel in d.modified:
            live = d.live_base / rel
            if live.is_file():
                yield d.entry, rel, live


def _head_items(manifest: Manifest, checkout: Path, roots: dict[str, Path],
                already: list[MergeItem], stage: Path | None,
                base_mode: str = "auto", base_override: bytes | None = None,
                base_label: str = "") -> list[MergeItem]:
    """Files whose live content differs from the COMMITTED blob -- single-file
    entries and directory-entry members alike (see _head_candidates).

    Materialises HEAD's version into the stage dir so the rest of the pipeline
    sees three ordinary files and needs no git awareness.
    """
    rc, _ = _git(["rev-parse", "--git-dir"], cwd=checkout)
    if rc != 0 or stage is None:
        return []
    seen = {i.label for i in already}
    out: list[MergeItem] = []
    for entry, rel, live in _head_candidates(manifest, checkout, roots):
        repo_path = f"{entry.repo}/{rel}" if rel else entry.repo
        p = subprocess.run(["git", "show", f"HEAD:{repo_path}"],
                           cwd=str(checkout), capture_output=True)
        if p.returncode != 0 or not p.stdout:
            continue
        if _normalize_eol(p.stdout) == _normalize_eol(live.read_bytes()):
            continue
        item = MergeItem(entry=entry, rel=rel, live=live, repo=Path(), base=None)
        if item.label in seen:
            continue
        seen.add(item.label)
        stage.mkdir(parents=True, exist_ok=True)
        theirs = stage / (item.label.replace("/", "__").replace("\\", "__") + ".head")
        theirs.write_bytes(p.stdout)
        item.repo = theirs                     # content of theirs (staged)
        item.repo_dest = checkout / repo_path  # where it actually installs
        rej: list = []
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
        if found is not None:
            blob, sha = found
            base_f = stage / (theirs.stem + f".base-{sha}")
            base_f.write_bytes(blob)
            item.base = base_f
        elif rej:
            blob, sha, n, ratio = rej[0]
            name = (f".base-SIBLING-{sha}" if base_mode == "sibling"
                    else f".SIBLING-NOT-A-BASE-{sha}")
            sib = stage / (theirs.stem + name)
            sib.write_bytes(blob)
            item.sibling = (sib, sha, n, ratio)
            # Opt-in: the user may prefer a merge that pre-resolves more, at
            # the cost of deletions invented for content one side never had.
            if base_mode == "sibling":
                item.base = sib
        if entry.strategy in MERGE_REFUSED_STRATEGIES:
            layers = ", ".join([entry.repo, *entry.overlays]) or entry.repo
            item.reason = (f"'{entry.strategy}' composes its target; merge the "
                           f"source layer instead ({layers})")
        elif entry.strategy not in MERGEABLE_STRATEGIES:
            item.reason = f"unknown strategy '{entry.strategy}' -- refusing to merge"
        out.append(item)
    return out


def resolution_hints(item: MergeItem) -> list[str]:
    """Signals that tell a user which side to pick, cheapest first.

    Ordered deliberately. A deterministic rule beats a heuristic, and a
    heuristic beats a model:

    1. REGRESSED PATTERN -- one side carries text that was fixed locally.
       Decisive, no judgement required.
    2. CONVENTION -- one side systematically uses a form the other lacks
       (7 uses of ${CLAUDE_USER_DIR} vs 0). Strong, and inferable from the
       two files alone.
    3. COMMIT DATE -- which side was authored more recently. Usable, but only
       a proxy: newer is not automatically correct, and upstream being newer
       than a local edit you still want is the obvious counter-case.

    NOT included: file mtime. `theirs` is materialised from git at merge time,
    so its mtime is always "seconds ago"; the live file's mtime is whenever
    `ccs collect` last touched it. Both are artefacts of the tooling rather
    than facts about the content.
    """
    hints: list[str] = []
    ours, theirs = _text_of(item.live), _text_of(item.repo)
    if not ours or not theirs:
        return hints
    for pat in DEFAULT_REGRESSED_PATTERNS:
        o, t = ours.count(pat), theirs.count(pat)
        if o != t:
            side = "theirs" if t > o else "ours"
            hints.append(f"{pat!r} appears {max(o, t)}x on {side} and "
                         f"{min(o, t)}x on the other -- it was fixed locally, "
                         f"so prefer the side WITHOUT it")
    # One token only: "$HOME/claude" is a substring of
    # "${CLAUDE_USER_DIR:-$HOME/claude}", so checking both reported the
    # same 7 occurrences twice.
    for token in ("${CLAUDE_USER_DIR",):
        o, t = ours.count(token), theirs.count(token)
        if o and not t:
            hints.append(f"ours uses {token}...}} {o}x, theirs 0x -- ours "
                         "carries a convention theirs predates")
        elif t and not o:
            hints.append(f"theirs uses {token}...}} {t}x, ours 0x")
    return hints


def base_phantom_ratio(base: bytes, ours: bytes, theirs: bytes) -> tuple[float, int]:
    """How much of "ours deleted this" is really "ours never had this".

    A valid base is an ANCESTOR of ours, so base->ours deletions are our own
    edits. When the other side still holds those exact lines, the likelier
    story is that the "base" is a SIBLING of theirs that we never descended
    from -- and the merge then applies a phantom deletion against content we
    never removed.

    Only PURE `delete` opcodes count. A `replace` is ours editing a line
    theirs left alone (our ${CLAUDE_USER_DIR} conversion, say); theirs
    retaining the old text there is expected and proves nothing.

    Measured on the real fixture: 6 replaced lines (legitimate) and 10 purely
    deleted lines, all 10 retained verbatim by theirs -- and those 10 were
    exactly the Key-footer intro, whose loss made the rest incoherent.
    """
    b = _normalize_eol(base).decode("utf-8", "replace").splitlines()
    o = _normalize_eol(ours).decode("utf-8", "replace").splitlines()
    t = {l.strip() for l in
         _normalize_eol(theirs).decode("utf-8", "replace").splitlines() if l.strip()}
    deleted = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, b, o, autojunk=False).get_opcodes():
        if tag == "delete":
            deleted += [l for l in b[i1:i2] if l.strip()]
    if not deleted:
        return (0.0, 0)
    retained = sum(1 for l in deleted if l.strip() in t)
    return (retained / len(deleted), len(deleted))


# A base is rejected when it attributes at least this many purely-deleted
# lines to us AND the other side still holds this fraction of them.
PHANTOM_MIN_LINES = 3
PHANTOM_RATIO = 0.8


def infer_base(checkout: Path, repo_path: str, ours: bytes, theirs: bytes,
               max_commits: int = 25,
               rejected: list | None = None) -> tuple[bytes, str] | None:
    """Best-effort ancestor for a live-vs-checkout merge.

    Nothing records which commit a live tree was last synced to, so the
    base is ESTIMATED from the checkout's history. The estimate follows
    five rules, each traceable to a measured failure (2026-08-21; scenario
    classes SC-nn are from the scenario-space DWP):

      1. HEAD is a candidate. HEAD *is* the sync point in the most common
         workflow -- apply, then edit live (SC-11a). Excluding it meant every
         ordinary post-sync edit of a single-file entry was refused as
         two-sided; that is why every refusal anyone saw was CLAUDE.md.
         But HEAD must beat at least one OLDER candidate: with a one-commit
         history "HEAD is nearest" is tautological, indistinguishable from
         adoption (SC-80), and refused.
      2. A candidate equal to OURS is returned at once. Live then holds
         nothing unique relative to it -- distance zero, proof not guess.
         Skipping such candidates labelled 22 one-sided files "both" (SC-10).
      3. A candidate equal to THEIRS (HEAD included) is exempt from the
         phantom check -- every deletion is "retained by theirs" by
         definition there, so the check would reject the true base whenever
         live deleted >= 3 lines (SC-12) -- but it must win the distance
         contest STRICTLY. A revert makes HEAD equal an older commit while
         the checkout genuinely moved (SC-14); and on an exact tie HEAD would
         otherwise win by an immunity it did not earn (scenario E: live
         deleted three lines, checkout replaced the same three).
      4. The nearest candidate is chosen over ALL candidates, rejected ones
         included. If the nearest was phantom-rejected, return None: a farther
         base is a wrong base, and falling back to HEAD turned refusals into
         silent passes (SC-22).
      5. The phantom check stays for dissimilar candidates: a "base" that
         attributes deletions to us which theirs still holds is likelier a
         sibling than an ancestor.

    Returning None is an honest answer: refuse, degrade to a two-way hand-off,
    never invent a third input. Known residual, undecidable from the two files
    and this history: adoption (no sync point exists) and a byte-exact hand
    revert of live -- both belong to `adopt` / a recorded sync point.
    """
    norm = _normalize_eol
    ours_n, theirs_n = norm(ours), norm(theirs)
    rc, out = _git(["log", "--format=%H", "--follow", "--", repo_path], cwd=checkout)
    if rc != 0 or not out:
        return None
    shas = out.split()[:max_commits + 1]
    rc_h, head = _git(["rev-parse", "HEAD"], cwd=checkout)
    head = head.strip() if rc_h == 0 else ""
    if head and head not in shas:
        shas.insert(0, head)          # `git log -- path` omits a TREESAME merge commit at HEAD

    scored: list[tuple[int, bytes, str, bool, bool]] = []   # (score, blob, sha7, rejected, eq_theirs)
    for sha in shas:
        p = subprocess.run(["git", "show", f"{sha}:{repo_path}"],
                           cwd=str(checkout), capture_output=True)
        if p.returncode != 0 or not p.stdout:
            continue
        cand = norm(p.stdout)
        if cand == ours_n:
            return (p.stdout, sha[:7])                               # rule 2
        eq_theirs = cand == theirs_n
        is_rejected = False
        if not eq_theirs:                                            # rule 3 (exemption)
            ratio, n = base_phantom_ratio(cand, ours_n, theirs_n)
            if n >= PHANTOM_MIN_LINES and ratio >= PHANTOM_RATIO:   # rule 5
                is_rejected = True
                if rejected is not None:
                    rejected.append((p.stdout, sha[:7], n, ratio))
        sm = difflib.SequenceMatcher(
            None, cand.decode("utf-8", "replace").splitlines(),
            ours_n.decode("utf-8", "replace").splitlines(), autojunk=False)
        score = sum((i2 - i1) + (j2 - j1)
                    for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal")
        scored.append((score, p.stdout, sha[:7], is_rejected, eq_theirs))

    if not scored:
        return None
    if all(s[4] for s in scored) and len(scored) == 1:
        # HEAD is the ONLY candidate (single-commit history). "HEAD is nearest"
        # is then tautological, not evidence: this is indistinguishable from
        # adoption -- a live tree that never synced from here -- and unknown is
        # not the same as safe. Refuse; a recorded sync point or `adopt` decides.
        return None
    best_score = min(s[0] for s in scored)
    at_best = [s for s in scored if s[0] == best_score]
    # rule 3 (strict win): on a tie, a theirs-equal candidate loses
    others = [s for s in at_best if not s[4]]
    nearest = (others or at_best)[0]
    if nearest[3]:                                                   # rule 4
        return None
    return (nearest[1], nearest[2])


def _items_for_diff(d: EntryDiff) -> list[MergeItem]:
    out: list[MergeItem] = []
    strategy = d.entry.strategy
    for rel in d.modified:
        live = d.live_base / rel if rel else d.live_base
        repo = d.repo_base / rel if rel else d.repo_base
        item = MergeItem(entry=d.entry, rel=rel, live=live, repo=repo,
                         repo_dest=repo)
        if strategy in MERGE_REFUSED_STRATEGIES:
            # Name the source layer instead of merging the composed target.
            layers = ", ".join([d.entry.repo, *d.entry.overlays]) or d.entry.repo
            item.reason = (
                f"'{strategy}' composes its target; merge the source layer "
                f"instead ({layers})")
        elif strategy not in MERGEABLE_STRATEGIES:
            item.reason = f"unknown strategy '{strategy}' -- refusing to merge"
        out.append(item)
    return out


# --------------------------------------------------------------------------
# Tool resolution
# --------------------------------------------------------------------------

def _git(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


def _executable_of(cmd: str) -> str | None:
    """First token of a mergetool cmd string, honouring quotes.

    `mergetool.<t>.cmd` is a shell string; the binary may be a quoted absolute
    path with spaces ("C:\\app\\diff\\Beyond Compare 4\\bcomp.exe").
    """
    try:
        parts = shlex.split(cmd, posix=False)
    except ValueError:
        return None
    return parts[0].strip('"') if parts else None


# Tools git drives WITHOUT a mergetool.<name>.cmd entry -- its built-ins,
# whose invocations live in git's own mergetools/ scripts. ccs carries the
# common terminal and desktop ones so `ccs merge --tool vimdiff` works on a
# box that has vim and nothing else (a server, typically) with no git config
# at all. Same $LOCAL/$BASE/$REMOTE/$MERGED contract, expanded by substitute().
# A configured mergetool.<name>.cmd always wins over this table.
BUILTIN_TOOLS: dict[str, str] = {
    # git's vimdiff layout: LOCAL | BASE | REMOTE on top, MERGED below
    "vimdiff": 'vim -f -d -c "4wincmd w | wincmd J" "$LOCAL" "$BASE" "$REMOTE" "$MERGED"',
    "nvimdiff": 'nvim -f -d -c "4wincmd w | wincmd J" "$LOCAL" "$BASE" "$REMOTE" "$MERGED"',
    "meld": 'meld --auto-merge "$LOCAL" "$BASE" "$REMOTE" --output "$MERGED"',
    "kdiff3": 'kdiff3 --auto "$BASE" "$LOCAL" "$REMOTE" -o "$MERGED"',
}


# What a tool does with an EXISTING output file when it is handed one as
# $MERGED. This is a fact about the tool and is DECLARED, never inferred from
# the command string: a bare positional "$MERGED" says nothing -- BeyondCompare
# takes exactly that and regenerates its output pane from the three inputs on
# every load (measured; documented identically for BC4 and BC5), so reopening
# a file a person already resolved destroys the resolution. The tiers, in
# preference order; the floor is always available:
#
#   preloads        the tool shows the existing $MERGED  -- reopening is safe
#   preloads-with   the tool needs a documented flag to  -- no confirmed member yet
#   inject:<name>   ccs paints the content in after launch (profile <name>;
#                   Windows) -- declared here only once the driver ships
#   writes-only     the tool regenerates $MERGED -- a resumed file stays closed
#   unknown         not in this table: treated as writes-only, and the name
#                   to ask the person about
#
# A tool name absent here but present in git's mergetool.<name>.cmd is
# `unknown`, not an error -- the launch still works; only the reopen decision
# falls to the floor.
RESUME_PRELOADS = "preloads"
RESUME_PRELOADS_WITH = "preloads-with"
RESUME_WRITES_ONLY = "writes-only"
RESUME_UNKNOWN = "unknown"
RESUME_INJECT_PREFIX = "inject:"

# The fallback table, used when the packaged registry cannot be read. It must
# say the same thing as the registry's `tools` (a test holds them equal), so a
# packaging slip downgrades ccs to *unexplained profiles*, never to a wrong
# reopen decision.
_BUILTIN_RESUME: dict[str, str] = {
    "vimdiff": RESUME_PRELOADS,       # vim opens $MERGED as a buffer: what is there is shown
    "nvimdiff": RESUME_PRELOADS,
    "meld": RESUME_WRITES_ONLY,       # --output is a destination
    "kdiff3": RESUME_WRITES_ONLY,     # -o is a destination
    # git's own name for BeyondCompare (the only bc-* it ships). It regenerates
    # its output pane, so reopening is unsafe -- unless ccs paints the work
    # back in, which the bc5 profile knows how to do on Windows. Elsewhere the
    # profile does not apply and the tier is read as writes-only.
    "bc": RESUME_INJECT_PREFIX + "bc5",
}

# A configured tool can be called anything (`merge.tool = bc` here points at
# BC5's BComp.exe while `diff.tool = bc` points at BC4's BCompare.exe), so a
# name list is always incomplete. The behaviour belongs to the BINARY: when a
# name is not in the table, classify by the executable's basename, lower-cased,
# extension dropped. Measured for bcomp/bcompare (BC4 and BC5 alike).
_BUILTIN_EXE_RESUME: dict[str, str] = {
    "bcomp": RESUME_INJECT_PREFIX + "bc5",
    "bcompare": RESUME_INJECT_PREFIX + "bc5",
    "vim": RESUME_PRELOADS,
    "nvim": RESUME_PRELOADS,
    "meld": RESUME_WRITES_ONLY,
    "kdiff3": RESUME_WRITES_ONLY,
}

RESUME_TIERS = frozenset({RESUME_PRELOADS, RESUME_PRELOADS_WITH, RESUME_WRITES_ONLY})

#: The shipped registry: capabilities and injection profiles as DATA, so a
#: tool can be classified -- or a profile corrected -- without touching
#: Python. Same shipping contract as settings-explanations.json: package-data,
#: proven present by scripts/check-wheel-data.py against a built wheel.
TOOL_REGISTRY_FILE = "merge-tools.json"


_REGISTRY_SECTIONS = ("tools", "executables", "inject_profiles")


def _normalize_registry(body, label: str, *, require_tools: bool) -> tuple[dict, str | None]:
    """Shape-check a registry body into {tools, executables, inject_profiles}.

    The packaged file must carry `tools` (a package without it is broken);
    a user overlay may carry only the section it wants to override. Entries
    that are not objects are dropped, never trusted.
    """
    if not isinstance(body, dict):
        return {}, f"{label}: the top level is not a JSON object"
    out: dict = {}
    for section in _REGISTRY_SECTIONS:
        part = body.get(section)
        if part is None:
            if section == "tools" and require_tools:
                return {}, f"{label} has no 'tools' object"
            out[section] = {}
            continue
        if not isinstance(part, dict):
            return {}, f"{label}: '{section}' is not an object"
        out[section] = {n: v for n, v in part.items() if isinstance(v, dict)}
    return out, None


def load_tool_registry() -> tuple[dict, str | None]:
    """Read the packaged registry. Returns ({tools, executables,
    inject_profiles}, reason).

    Every failure degrades rather than raising: the launch still works and
    the reopen decision falls back to `_BUILTIN_RESUME`. `ccs doctor` shows
    the reason, so a packaging slip is visible instead of silently costing a
    profile.
    """
    import json
    try:
        from importlib import resources
        raw = (resources.files("dazzle_claude_config")
               .joinpath(TOOL_REGISTRY_FILE).read_text(encoding="utf-8"))
    except (OSError, ModuleNotFoundError, TypeError) as exc:
        return {}, f"{TOOL_REGISTRY_FILE} is not installed ({exc})"
    try:
        body = json.loads(raw)
    except ValueError as exc:
        return {}, f"{TOOL_REGISTRY_FILE} is not valid JSON ({exc})"
    return _normalize_registry(body, TOOL_REGISTRY_FILE, require_tools=True)


#: A user's own registry, in user territory beside ccs-config.json (the
#: ccs-box.json idiom). Same shape as the packaged file; any section may be
#: omitted; its entries win over the packaged ones. Missing means "nothing to
#: add"; malformed is reported for doctor and ignored -- a broken overlay must
#: not change a reopen decision.
USER_TOOLS_FILE = "merge-tools.json"


def user_tools_path(user_claude: Path | None = None) -> Path:
    """One resolver for user territory (platform_info), not a sixth `~/claude`."""
    return user_claude_dir(str(user_claude) if user_claude else None) / USER_TOOLS_FILE


def load_user_tool_registry(user_claude: Path | None = None) -> tuple[dict, list[str]]:
    """({tools, executables, inject_profiles} or {}, errors).

    A parse failure is phrased the way every other ccs reader phrases it
    (`not valid JSON (...)`), never as the parser's class name (#39)."""
    import json
    path = user_tools_path(user_claude)
    if not path.exists():
        return {}, []
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return {}, [f"{path}: cannot be read ({exc})"]
    try:
        body = json.loads(raw)
    except ValueError as exc:
        return {}, [f"{path}: {not_valid_json(exc)}"]
    reg, reason = _normalize_registry(body, str(path), require_tools=False)
    return (reg, []) if reason is None else ({}, [reason])


def effective_registry(user_claude: Path | None = None) -> tuple[dict, list[str]]:
    """The packaged registry with the user's overlay applied, per section,
    user entries winning. Returns (registry, errors-from-the-user-file)."""
    user, errors = load_user_tool_registry(user_claude)
    merged = {section: {**TOOL_REGISTRY.get(section, {}), **user.get(section, {})}
              for section in _REGISTRY_SECTIONS}
    return merged, errors


def _resume_table(registry: dict) -> dict[str, str]:
    """The fallback overlaid by the registry's `tools` (registry wins)."""
    table = dict(_BUILTIN_RESUME)
    for name, rec in registry.get("tools", {}).items():
        cap = rec.get("resume")
        if isinstance(cap, str) and cap:
            table[name] = cap
    return table


def _exe_table(registry: dict) -> dict[str, str]:
    """The fallback overlaid by the registry's `executables` (registry wins)."""
    table = dict(_BUILTIN_EXE_RESUME)
    for exe, rec in registry.get("executables", {}).items():
        cap = rec.get("resume")
        if isinstance(cap, str) and cap:
            table[exe.lower()] = cap
    return table


def _exe_key(cmd: str | None) -> str | None:
    """`"C:/Program Files/Beyond Compare 5/BComp.exe" ...` -> `bcomp`."""
    exe = _executable_of(cmd) if cmd else None
    if not exe:
        return None
    base = Path(exe.replace("\\", "/")).name.lower()
    for ext in (".exe", ".com", ".bat", ".cmd"):
        if base.endswith(ext):
            base = base[: -len(ext)]
    return base or None


TOOL_REGISTRY, TOOL_REGISTRY_ERROR = load_tool_registry()
TOOL_RESUME: dict[str, str] = _resume_table(TOOL_REGISTRY)
EXE_RESUME: dict[str, str] = _exe_table(TOOL_REGISTRY)
INJECT_PROFILES: dict[str, dict] = TOOL_REGISTRY.get("inject_profiles", {})


def tool_resume(name: str, registry: dict | None = None) -> str:
    """The declared resume capability of a tool: by name first, then by the
    executable its command runs (a configured tool under any name), else
    RESUME_UNKNOWN. `registry` is an `effective_registry()` result; None
    means the packaged view."""
    if registry is None:
        by_name, by_exe = TOOL_RESUME, EXE_RESUME
    else:
        by_name, by_exe = _resume_table(registry), _exe_table(registry)
    cap = by_name.get(name)
    if cap:
        return cap
    exe = _exe_key(tool_command(name))
    return by_exe.get(exe, RESUME_UNKNOWN) if exe else RESUME_UNKNOWN


def reopen_is_safe(name: str, registry: dict | None = None) -> bool:
    """True only when the tool is declared to show an existing $MERGED as-is.
    Everything else -- including a tool nobody has classified -- is the floor:
    a resumed file is not reopened, because the cost of being wrong is the
    person's work."""
    return tool_resume(name, registry) == RESUME_PRELOADS


def inject_profile(name: str, registry: dict | None = None) -> str | None:
    """The injection profile name declared for a tool, or None."""
    cap = tool_resume(name, registry)
    return cap[len(RESUME_INJECT_PREFIX):] if cap.startswith(RESUME_INJECT_PREFIX) else None


def _profile_applies(profile: dict) -> bool:
    """A profile is for one platform; elsewhere it is documentation."""
    want = profile.get("os")
    if not want:
        return True
    here = "windows" if sys.platform == "win32" else "posix"
    return want == here


def inject_profile_for(name: str, registry: dict | None = None) -> tuple[str, dict] | None:
    """(profile name, profile) when the tool declares an injection profile that
    exists and applies on this platform; else None."""
    pname = inject_profile(name, registry)
    if not pname:
        return None
    profiles = (registry or TOOL_REGISTRY).get("inject_profiles", {}) if registry is not None \
        else INJECT_PROFILES
    profile = profiles.get(pname)
    if not isinstance(profile, dict) or not _profile_applies(profile):
        return None
    return pname, profile


def effective_tier(name: str, registry: dict | None = None) -> str:
    """The tier that governs THIS run: an inject profile that does not exist
    or does not apply here collapses to writes-only -- the floor, never a
    silent promotion."""
    cap = tool_resume(name, registry)
    if cap.startswith(RESUME_INJECT_PREFIX):
        return cap if inject_profile_for(name, registry) else RESUME_WRITES_ONLY
    return cap


def tool_command(name: str) -> str | None:
    """The shell line for a tool: mergetool.<name>.cmd if configured, else
    the built-in table, else None."""
    rc, cmd = _git(["config", "--get", f"mergetool.{name}.cmd"])
    if rc == 0 and cmd:
        return cmd
    return BUILTIN_TOOLS.get(name)


def _tool_usable(name: str) -> bool:
    """A tool name is only usable if its binary actually exists.

    Measured: `merge.tool = bc` resolves to a bare `BCompare.exe` that is NOT
    on PATH, while `diff.tool = bc4` points at a real absolute path. Trusting
    the configured name would fail on the very machine that configured it, so
    the name is never trusted without a probe (AC-5). Built-ins are probed
    the same way: `vimdiff` is usable only where `vim` is.
    """
    cmd = tool_command(name)
    if not cmd:
        return False
    exe = _executable_of(cmd)
    if not exe:
        return False
    return bool(shutil.which(exe)) or Path(exe).exists()


def resolve_difftool(explicit: str | None = None) -> str:
    """Pick a TWO-pane diff tool, verifying its binary exists.

    Separate registry from mergetool: git keeps `difftool.<name>.cmd` and
    `mergetool.<name>.cmd` apart, and a name can exist in one and not the
    other. Measured on this machine: `bc4` is a difftool entry only, and
    `beyondcompare4` a mergetool entry only -- so reusing the merge resolver
    here would fail to find a working two-pane tool that is right there.
    """
    def usable(name: str) -> bool:
        rc, cmd = _git(["config", "--get", f"difftool.{name}.cmd"])
        if rc != 0 or not cmd:
            return False
        exe = _executable_of(cmd)
        return bool(exe) and (bool(shutil.which(exe)) or Path(exe).exists())

    if explicit:
        if not usable(explicit):
            raise MergeError(
                f"diff tool {explicit!r} is configured but its binary was not "
                f"found; check `git config difftool.{explicit}.cmd`")
        return explicit

    candidates: list[str] = []
    rc, configured = _git(["config", "--get", "diff.tool"])
    if rc == 0 and configured:
        candidates.append(configured)
    rc, out = _git(["config", "--get-regexp", r"^difftool\..*\.cmd$"])
    if rc == 0:
        names = [line.split()[0].split(".", 2)[1]
                 for line in out.splitlines() if line.strip()]
        candidates.extend(sorted(set(names), key=lambda n: (-len(n), n)))
    for name in candidates:
        if usable(name):
            return name
    raise MergeError("no usable diff tool found -- set `git config diff.tool`")


def launch_difftool(tool: str, left: Path, right: Path) -> int:
    """Open two files side by side. Same substitution rule as the merge path:
    cmd.exe does not expand $VAR, so ccs expands it rather than the shell."""
    if not interactive():
        raise MergeError(
            "no console attached -- refusing to launch an interactive diff tool")
    rc, cmd = _git(["config", "--get", f"difftool.{tool}.cmd"])
    if rc != 0 or not cmd:
        raise MergeError(f"no difftool.{tool}.cmd configured")
    line = cmd
    for name, value in (("LOCAL", str(left)), ("REMOTE", str(right))):
        line = line.replace("${" + name + "}", value).replace("$" + name, value)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    subprocess.Popen(line, shell=True, creationflags=flags)
    return 0


def resolve_tool(explicit: str | None = None) -> str:
    """Pick a merge tool, verifying it can actually run.

    Order: --tool, then git's own `merge.tool`, then any configured
    mergetool.* entry, preferring longer names because they tend to carry a
    version suffix ("beyondcompare4" over "bc") and the higher version is the
    better default guess.
    """
    if explicit:
        if not _tool_usable(explicit):
            hint = (f"check `git config mergetool.{explicit}.cmd`"
                    if explicit not in BUILTIN_TOOLS else
                    f"`{_executable_of(BUILTIN_TOOLS[explicit])}` is not on PATH")
            raise MergeError(
                f"merge tool '{explicit}' cannot run -- its binary was not found; {hint}")
        return explicit

    candidates: list[str] = []
    rc, configured = _git(["config", "--get", "merge.tool"])
    if rc == 0 and configured:
        candidates.append(configured)

    rc, out = _git(["config", "--get-regexp", r"^mergetool\..*\.cmd$"])
    if rc == 0:
        names = [line.split()[0].split(".", 2)[1]
                 for line in out.splitlines() if line.strip()]
        candidates.extend(sorted(set(names), key=lambda n: (-len(n), n)))

    # Built-ins last: a configured tool is an expressed preference, a
    # built-in is what happens to be installed. Terminal tools before desktop
    # ones, since the box most likely to have nothing configured is a server.
    candidates.extend(n for n in BUILTIN_TOOLS if n not in candidates)

    for name in candidates:
        if _tool_usable(name):
            return name
    raise MergeError(
        "no usable merge tool found -- install one git knows (vim, nvim, meld, "
        "kdiff3 need no config) or configure one, e.g.\n"
        '  git config --global mergetool.bc4.cmd '
        "'\"C:/path/to/bcomp.exe\" --wait \"$LOCAL\" \"$REMOTE\" \"$BASE\" \"$MERGED\"'")


# --------------------------------------------------------------------------
# Seeding and validation
# --------------------------------------------------------------------------

def seed(item: MergeItem, merged: Path, union: bool = False,
         cod_ratio: float | None = None) -> int:
    """Write the starting point for the output pane; return the conflict count.

    With a base this is a real 3-way. WITHOUT a base there is genuinely no
    third input, so rather than inventing one (the failure that started this
    whole design) we copy `ours` and report every difference as unresolved --
    an honest 2-way that the human arbitrates.
    """
    # Check inputs FIRST. A missing input makes `git merge-file` exit 255 with
    # empty output, which downstream reads as "255 conflicts" and an empty
    # result -- a rejection for entirely the wrong reason. That is a false
    # pass, and it happened during development: the gate looked correct while
    # actually being fed a file that did not exist.
    for role, p_ in (("ours", item.live), ("theirs", item.repo), ("base", item.base)):
        if p_ is not None and not p_.is_file():
            raise MergeError(f"{role} input not found: {p_}")

    if item.base_supplied and not union:
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
        merged.write_bytes("\n".join(out).encode("utf-8"))
        item.cod = stats
        return stats.hunks
    if item.base is None:
        if not union:
            merged.write_bytes(item.live.read_bytes())
            return 1 if _differs(item.live, item.repo) else 0
        # No ancestry, but the user asked to keep both sides. The EMPTY tree is
        # the honest base: it says "neither side had anything", so every line on
        # each side reads as an addition and NOTHING is ever inferred as a
        # deletion. That is exactly right for unrelated trees, and far safer
        # than a sibling commit -- which fabricates deletions for content one
        # side simply never had. Measured: a sibling base dropped the 10-line
        # Key-footer intro and made the surviving section incoherent.
        tmp = merged.parent / (merged.name + ".inputs")
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp / "empty").write_bytes(b"")
        for role, src in (("ours", item.live), ("theirs", item.repo)):
            (tmp / role).write_bytes(_normalize_eol(src.read_bytes()))
        p = subprocess.run(["git", "merge-file", "-p", "--union",
                            str(tmp / "ours"), str(tmp / "empty"),
                            str(tmp / "theirs")], capture_output=True)
        if p.returncode < 0 or p.returncode >= 128:
            raise MergeError(f"git merge-file failed ({p.returncode})")
        merged.write_bytes(p.stdout)
        return p.returncode

    # NORMALISE LINE ENDINGS FIRST. The live tree is CRLF on Windows while
    # git hands back LF, so without this EVERY line differs and the merge
    # collapses into one enormous conflict. Measured on the real fixture:
    #
    #   raw        1983 lines, ~1000 inside a single conflict  (unusable)
    #   normalised 1019 lines,     50 inside one conflict (4%) (reviewable)
    #
    # 95% of the file auto-resolves once the comparison is like-for-like.
    # This is AC-15: an EOL-only difference must never manufacture a merge.
    tmp = merged.parent / (merged.name + ".inputs")
    tmp.mkdir(parents=True, exist_ok=True)
    norm_paths = []
    for role, src in (("ours", item.live), ("base", item.base), ("theirs", item.repo)):
        f = tmp / role
        f.write_bytes(_normalize_eol(src.read_bytes()))
        norm_paths.append(str(f))

    # --union resolves conflicting regions by KEEPING BOTH sides instead of
    # emitting markers. It is the right answer when the two sides added
    # different things and the "conflict" is an artefact of an over-reaching
    # base -- which is exactly this project's situation: the inferred base is
    # the earliest commit available, and it already contains content the other
    # machine never had, so git reads "we never had it" as "we deleted it".
    # Opt-in only: union silently keeps both, so it must never be the default.
    flags = ["--union"] if union else ["--diff3"]
    p = subprocess.run(["git", "merge-file", "-p", *flags, *norm_paths],
                       capture_output=True)
    # git merge-file returns the conflict count, or -1 (surfacing as 255 on
    # Windows) when the merge itself failed. Treating that as a count would
    # report "255 conflicts" for what is actually a hard error.
    if p.returncode < 0 or p.returncode >= 128:
        err = p.stderr.decode("utf-8", errors="replace").strip()
        raise MergeError(f"git merge-file failed ({p.returncode}): {err or 'no detail'}")
    merged.write_bytes(p.stdout)
    return p.returncode


def _differs_bytes(a: Path, b: Path) -> bool:
    """Exact byte comparison -- used to tell 'freshly seeded' from 'edited'."""
    try:
        return a.read_bytes() != b.read_bytes()
    except OSError:
        return True


def _differs(a: Path, b: Path) -> bool:
    try:
        return _normalize_eol(a.read_bytes()) != _normalize_eol(b.read_bytes())
    except OSError:
        return True


def _lines(p: Path | None) -> set[str]:
    if p is None or not p.exists():
        return set()
    try:
        text = _normalize_eol(p.read_bytes()).decode("utf-8", errors="replace")
    except OSError:
        return set()
    return {ln.strip() for ln in text.splitlines() if ln.strip()}


def validate(item: MergeItem, merged: Path,
             probes: dict[str, str] | None = None,
             regressed: tuple[str, ...] = DEFAULT_REGRESSED_PATTERNS,
             ) -> ValidationResult:
    """The gate. Nothing is written back unless this passes.

    Checks, each traceable to a measured failure:
      1. no leftover conflict markers        -- an unresolved merge is not a merge
      2. no invented content                 -- every line traceable to an input
      3. no regressed patterns               -- upstream may carry what we fixed
      4. named content survived              -- the explicit "did we lose it" test
      5. no credential-shaped content        -- merged output re-enters the guard
    """
    res = ValidationResult()
    if not merged.exists():
        res.failures.append("merged file was not produced")
        return res

    raw = _normalize_eol(merged.read_bytes())
    text = raw.decode("utf-8", errors="replace")

    for marker in CONFLICT_MARKERS:
        if any(ln.startswith(marker) for ln in text.splitlines()):
            res.failures.append(f"unresolved conflict markers ({marker})")
            break

    # A result byte-identical to ONE input, when the inputs differ, is not a
    # merge -- it is a silent choice, and it is both costumes of the invariant
    # at once. This check needs no configuration, which is the point: the
    # named-probe check below only runs when a caller supplies probes, and the
    # CLI initially did not. That gap let a run where the tool wrote nothing
    # report "merged" while the other side contributed zero content.
    # Ask what was LOST, not what the result resembles. A merge whose output
    # equals ours is perfectly valid when the other side had nothing unique to
    # contribute -- that is a no-op merge, not a failed one. Testing byte
    # identity instead rejected exactly that case: a file where theirs was a
    # subset of ours failed with "the other side contributed nothing", which
    # was true and yet not a problem.
    # SUPERSEDED is not LOST. A line the other side holds may be an older
    # wording that our side rewrote -- "Two of these are re-entrant" against
    # "Three of these...". Keeping both would make the document contradict
    # itself, so its absence is correct. Only PURE deletions count as loss:
    # a `replace` region means the result carries replacement text in that
    # position, while a `delete` region means the content simply vanished.
    for side, src in (("theirs", item.repo), ("ours", item.live)):
        other = item.live if side == "theirs" else item.repo
        if not src.is_file() or not other.is_file():
            continue
        src_lines = _normalize_eol(src.read_bytes()).decode("utf-8", "replace").splitlines()
        res_lines = text.splitlines()
        dropped: list[str] = []
        regions: list[list[str]] = []   # the same lines, grouped by opcode region
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                None, src_lines, res_lines, autojunk=False).get_opcodes():
            if tag == "delete":
                reg = [l for l in src_lines[i1:i2] if l.strip()]
                dropped += reg
                regions.append(reg)
            elif tag == "replace":
                # A replacement may be a REWRITE of the same statement, or
                # unrelated text that merely aligned there. Similarity tells
                # them apart: "Two of these are re-entrant..." against "Three
                # of these are re-entrant..." is a rewrite; "THEIRS-SECTION-A"
                # against "OURS-ONLY" is a clobber. Only clobbers are loss.
                gone, came = src_lines[i1:i2], res_lines[j1:j2]
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
        # A line that is PRESENT in the result -- anywhere -- was not lost. The
        # opcode walk pairs lines by position, so a reviewer who moved a line
        # past its neighbour showed up as one delete plus one insert and was
        # charged with losing a line they had merely reordered (tester run-01).
        present = {l.strip() for l in res_lines if l.strip()}

        def _counts(l: str) -> bool:
            return (l.strip() not in other_lines
                    and l.strip() not in present
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
                f"{_LOSS_PREFIX} {len(dropped)} line(s) that {_side_name(side)} has are "
                f"missing from the result, not replaced "
                f"(first: {_excerpt(sorted(dropped)[0], 70)!r})")

    known = _lines(item.live) | _lines(item.repo) | _lines(item.base)
    if known:
        invented = [ln for ln in {l.strip() for l in text.splitlines() if l.strip()}
                    if ln not in known and not ln.startswith(CONFLICT_MARKERS)]
        if invented:
            res.failures.append(
                f"{len(invented)} line(s) in the result appear in neither side "
                f"nor the base (first: {_excerpt(invented[0], 60)!r})")

    # --union keeps BOTH sides, so its characteristic failure is duplication:
    # a paragraph present in ours and theirs can land twice. Only substantial
    # lines are checked -- blanks, fences and short list markers legitimately
    # repeat throughout a document.
    # Skip while markers remain: a hunk legitimately shows a region twice (the
    # ours and base panes), and the marker failure above already says it all.
    has_markers = any(f.startswith("unresolved conflict markers") for f in res.failures)
    merged_lines = [] if has_markers else         [l.strip() for l in text.splitlines() if len(l.strip()) > 40]
    from collections import Counter
    mc = Counter(merged_lines)
    oc = Counter(l.strip() for l in _text_of(item.live).splitlines() if len(l.strip()) > 40)
    tc = Counter(l.strip() for l in _text_of(item.repo).splitlines() if len(l.strip()) > 40)
    dupes = [l for l, n in mc.items() if n > max(oc.get(l, 0), tc.get(l, 0))]
    if dupes:
        res.failures.append(
            f"{len(dupes)} line(s) appear more often than on either side -- "
            f"content was duplicated (first: {_excerpt(dupes[0], 60)!r})")

    for pat in regressed:
        n = text.count(pat)
        if n:
            res.failures.append(
                f"regressed pattern {pat!r} present {n}x -- it was fixed locally "
                "and must not return via merge")

    for name, needle in (probes or {}).items():
        present = needle in text
        res.survived[name] = present
        if not present and (needle in _text_of(item.live)
                            or needle in _text_of(item.repo)):
            res.failures.append(f"content lost: {name!r} was present on an input side")

    if is_denied(item.label) is None:
        hits = scan_file(merged, item.label)
        if hits:
            res.failures.append(
                f"credential-shaped content at line {hits[0].line_no} -- refusing")
    return res


def _text_of(p: Path | None) -> str:
    if p is None or not p.exists():
        return ""
    try:
        return _normalize_eol(p.read_bytes()).decode("utf-8", errors="replace")
    except OSError:
        return ""


# --------------------------------------------------------------------------
# Hand-off
# --------------------------------------------------------------------------

@dataclass
class MergeResult:
    """Outcome of one `ccs merge` run, for the CLI to render."""
    refused: list[MergeItem] = field(default_factory=list)
    resolved: list[MergeItem] = field(default_factory=list)
    unresolved: list[tuple[MergeItem, ValidationResult]] = field(default_factory=list)
    planned: list[MergeItem] = field(default_factory=list)   # --dry-run
    resumed: list[MergeItem] = field(default_factory=list)   # kept prior edits
    backup_dir: Path | None = None                           # originals, pre-install
    previewed: list[MergeItem] = field(default_factory=list)  # shown, not decided
    workspace: Path | None = None
    no_base: list[MergeItem] = field(default_factory=list)
    siblings: list[MergeItem] = field(default_factory=list)
    # Items a human accepted knowing lines would be dropped (no base, so the
    # tool could not tell a deliberate deletion from an accident; the person
    # could). Also in `resolved`; listed here so the CLI can say so.
    accepted_with_loss: list[tuple[MergeItem, ValidationResult]] = field(default_factory=list)
    # Resolved items whose validation honoured deletions (see ValidationResult).
    honoured: list[tuple[MergeItem, ValidationResult]] = field(default_factory=list)
    # Supplied-base items installed live-only (the checkout stays at HEAD).
    adopted: list[MergeItem] = field(default_factory=list)
    # -- the resume story, per tool capability (0.5.17) ------------------------
    tool: str | None = None            # the resolved tool name, for the CLI's wording
    tier: str | None = None            # its effective tier on this platform
    registry_errors: list[str] = field(default_factory=list)  # user overlay problems
    reopened: list[MergeItem] = field(default_factory=list)   # resumed, reopened: the tool preloads
    injected: list[MergeItem] = field(default_factory=list)   # resumed, reopened, work painted back in
    inject_refused: list[tuple[MergeItem, str]] = field(default_factory=list)  # not launched, and why
    inject_failed: list[tuple[MergeItem, str]] = field(default_factory=list)   # launched, paint failed
    restored: list[MergeItem] = field(default_factory=list)   # the tool saved over an unverified paint; put back
    discarded: list[MergeItem] = field(default_factory=list)  # --relaunch --discard: reopened without the work
    tool_exit: dict[str, int] = field(default_factory=dict)   # label -> the tool's exit code


def two_way_labels(manifest: Manifest, checkout: Path,
                   roots: dict[str, Path], box_tags=frozenset(),
                   only: str | None = None) -> list[str]:
    """Files that changed on BOTH sides, so a one-way copy would lose work.

    `collect` and `apply` treat "differs" as ordinary work: both bucket
    `d.modified` in with the safe one-sided cases and copy straight over it.
    For a file whose two sides each hold unique content that is silent data
    loss reported as success -- measured at 50 lines of CLAUDE.md.

    Detection needs the base, because two states cannot distinguish "they
    added" from "we deleted". A file with no recoverable base is reported too:
    unknown is not the same as safe.

    Coverage is PER-FILE via diff_all, so directory-target entries (skills/,
    commands/ -- the dominant real payload shape) are protected the same as
    single-file entries. Until 0.4.x this guard checked is_file() on the entry
    target and silently skipped every directory entry (TW1, checklist run-03:
    a real collect overwrote a committed diverged edit and exited 0).

    Seed-if-absent entries are deliberately NOT guarded: `collect` never
    touches them (diff_all covers copy entries only) and `apply` never
    overwrites an existing live file for them, so neither one-way verb can
    destroy a diverged seed -- refusing the whole run over one was pure
    over-refusal. `ccs merge` still offers them via the HEAD axis.

    RESOLVED files never appear here: diff_all's `modified` list is built with
    files_differ, which is EOL-normalized, so a file whose live copy matches
    the checkout's WORKING TREE (a finished merge; HEAD moves only on commit)
    is filtered before this function sees it.
    """
    out: list[str] = []
    for d in diff_all(manifest, checkout, roots, box_tags):
        if d.mismatch:
            continue
        reached, sub = only_scope(only, d.entry.repo)
        if not reached:
            continue
        d = scope_diff(d, sub)
        entry = d.entry
        for rel in d.modified:
            live = d.live_base / rel if rel else d.live_base
            if not live.is_file():
                continue
            repo_path = f"{entry.repo}/{rel}" if rel else entry.repo
            p_ = subprocess.run(["git", "show", f"HEAD:{repo_path}"],
                                cwd=str(checkout), capture_output=True)
            if p_.returncode != 0 or not p_.stdout:
                continue
            ours, theirs = _normalize_eol(live.read_bytes()), _normalize_eol(p_.stdout)
            if ours == theirs:
                continue
            found = infer_base(checkout, repo_path, ours, theirs)
            label = f"{entry.target}/{rel}" if rel else entry.target
            if found is None:
                out.append(label)             # no base -> cannot prove it is safe
                continue
            base = _normalize_eol(found[0])
            if base != ours and base != theirs:   # each side moved away from the base
                out.append(label)
    return out


def workspace_for(roots: dict[str, Path]) -> Path:
    """Scratch area for merge inputs -- USER territory, never the payload repo.

    Writing into the checkout would make the merge's own temporaries look like
    payload drift on the next status.
    """
    return roots["USER_CLAUDE"] / "merge" / "ccs"


def run(manifest: Manifest, checkout: Path, roots: dict[str, Path], *,
        tool: str | None = None, dry_run: bool = False, accept: bool = False,
        only: str | None = None, probes: dict[str, str] | None = None,
        union: bool = False, launch_tool: bool = True, relaunch: bool = False,
        discard: bool = False, inject_mode: str = "ask", confirm_inject=None,
        preview: bool = False, base_mode: str = "auto",
        confirm_loss=None, base_override: bytes | None = None,
        base_label: str = "", cod_ratio: float | None = None,
        box_tags=frozenset(), repo=None) -> MergeResult:
    """Plan, seed, validate and (optionally) hand off each divergent file.

    `box_tags` and `repo` feed the seed-state check below; `status` passes the
    same two, so the two verbs read one answer. Both are optional: without
    tags, tag-gated seeds are simply not classified (and stay planned, as
    before); without `repo`, `untouched-old` reads as `open`.

    `confirm_loss(item, validation) -> bool` is asked, once per file, when a
    result with NO base fails only because lines unique to one side were
    dropped and a human resolved the file in a tool. Without a base the tool
    cannot tell a deliberate deletion from an accident -- the person who just
    reviewed the file can -- so the default shows the lines and asks on a
    console, and refuses anywhere a console is absent.

    Nothing is written back unless `accept` is set AND validation passed. The
    workspace survives either way so a run can be inspected, re-run, or thrown
    away without touching the live tree.
    """
    res = MergeResult()
    ws = workspace_for(roots)
    ws.mkdir(parents=True, exist_ok=True)
    res.workspace = ws

    items = plan(manifest, checkout, roots, stage=ws, base_mode=base_mode,
                 base_override=base_override, base_label=base_label)
    if only:
        def _reached(i):
            ok, sub = only_scope(only, i.entry.repo)
            return ok and rel_in_scope(i.rel, sub)
        items = [i for i in items if _reached(i)]

    # A seeded file the person already owns is not merge work. `status`
    # reports seed-if-absent entries through the seed-decision record (#27)
    # and calls a kept one settled; until 2026-09-02 this loop never read that
    # record, so a bare `ccs merge` planned -- and opened a diff tool on --
    # files status had just called "yours", with the empty committed seed as
    # the base against the person's real file: one wrong-side save from
    # wiping it (measured on settings.local.json; the modularity design's G21,
    # third instance). Refuse those VISIBLY, as `refused <label> -- ...`. An
    # explicit scope (--only, or the positional) is the person naming the
    # file and lifts the refusal; `open` and `reopened` stay mergeable --
    # those are the states where a decision is genuinely pending.
    if only is None and any(i.entry.strategy == "seed-if-absent" and i.mergeable
                            for i in items):
        states = {t: s for t, s, _live, _repo in seeddecisions.findings(
            manifest, checkout, roots, repo, box_tags, roots.get("USER_CLAUDE"))[0]}
        for i in items:
            if i.entry.strategy != "seed-if-absent" or not i.mergeable or i.rel:
                continue
            st = states.get(i.entry.target)
            if st in seeddecisions.SETTLED:
                i.reason = (f"seeded and {st}: yours since delivery, so not merged "
                            f"unless you name it (ccs merge {i.label}); "
                            f"`ccs seed` re-asks the question")
    if base_override is not None and union:
        raise MergeError("--union keeps both sides without review, which is the opposite "
                         "of an adoption merge: a supplied base is merged conflict-on-delete "
                         "so every removal is a hunk you decide. Drop --union.")
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

    res.refused = [i for i in items if not i.mergeable]
    mergeable = [i for i in items if i.mergeable]
    if dry_run:
        res.planned = mergeable
        return res
    if not mergeable:
        return res
    resolved_tool = resolve_tool(tool) if (not dry_run and launch_tool) else None
    registry, res.registry_errors = effective_registry(roots["USER_CLAUDE"])
    res.tool = resolved_tool
    res.tier = effective_tier(resolved_tool, registry) if resolved_tool else None
    # Loop-invariant: the tool does not change per item, so its profile and
    # reopen safety are resolved once here rather than rebuilt per file.
    prof = inject_profile_for(resolved_tool, registry) if resolved_tool else None
    safe_reopen = reopen_is_safe(resolved_tool, registry) if resolved_tool else False

    for item in mergeable:
        safe = item.label.replace("/", "__").replace("\\", "__")
        merged = ws / f"{safe}.merged"
        empty = ws / ".empty-base"
        if item.base is None:
            empty.write_bytes(b"")
            res.no_base.append(item)
        if item.sibling is not None:
            res.siblings.append(item)
        # Compare the output pane against WHAT WE SEEDED, recorded in a
        # sidecar. Comparing it against `ours` was wrong in both directions:
        # a union seed never equals ours, so every re-run reported "resumed"
        # even when nothing had been touched -- and whenever an edited result
        # did coincide with ours it was mistaken for a fresh seed and
        # overwritten, discarding real work done in the diff tool.
        stamp = merged.parent / (merged.name + ".seed")
        if not merged.exists() or (stamp.exists()
                                   and not _differs_bytes(merged, stamp)):
            seed(item, merged, union=union, cod_ratio=cod_ratio)   # absent, or untouched
            stamp.write_bytes(merged.read_bytes())
        else:
            res.resumed.append(item)          # human edits present: keep them
        # DO NOT reopen a file we just decided to resume. The merge tool is
        # handed `merged` as its OUTPUT pane, and the common ones treat that
        # as a destination rather than an input: BeyondCompare's documented
        # form is `bcomp <Left> <Right> <Center> <Output>`, its complete list
        # of Merge Options has no switch to load an existing output, and it
        # was MEASURED regenerating the pane from the three inputs over a
        # maintainer's saved edits.
        #
        # So relaunching destroys exactly what resuming preserved, and the
        # report said "kept your prior edits" while the tool discarded them.
        # It also breaks the resume story the rest of this function is built
        # on: a person merging a hundred files could never stop and continue,
        # because every re-run reopened -- and therefore re-created -- the
        # work already done. `--relaunch` opts back in.
        #
        # Since 0.5.17 the decision reads the tool's DECLARED capability:
        #   preloads     -- reopen freely, even without --relaunch (vimdiff)
        #   inject:<p>   -- on --relaunch, reopen and paint the work back in;
        #                   `--discard` is the old destructive reopen, named
        #   writes-only  -- reopen only on --relaunch (unchanged, destructive)
        resumed_item = item in res.resumed
        if (launch_tool and not preview and resumed_item and relaunch
                and not discard and prof is not None):
            rc = _inject_flow(res, item, merged, item.base or empty, resolved_tool,
                              prof[0], prof[1], inject_mode, confirm_inject, ws)
            if rc is not None:
                res.tool_exit[item.label] = rc
        elif launch_tool and (not resumed_item or relaunch or safe_reopen):
            if resumed_item and not relaunch:
                res.reopened.append(item)          # the tool preloads: safe
            elif resumed_item and discard:
                res.discarded.append(item)
            rc = launch(resolved_tool, item, merged, item.base or empty,
                        wait=not preview)
            if not preview:
                res.tool_exit[item.label] = rc
        if preview:
            # Look, do not decide. Nothing is validated and nothing is
            # installed -- this exists so a user can SEE the three sides in
            # their own tool before committing to a resolution.
            res.previewed.append(item)
            continue
        v = validate(item, merged, probes=probes)
        # "A human resolved it" = a tool was launched, or the workspace file
        # carries edits since seeding (the headless flow: edit, re-run).
        human = launch_tool or item in res.resumed
        if not v.ok and item.base is None and v.only_loss and human and not union:
            ask = confirm_loss or _ask_loss_on_console
            if ask(item, v):
                res.accepted_with_loss.append((item, v))
                v = ValidationResult(honoured=v.honoured, lost=v.lost)
        if not v.ok:
            res.unresolved.append((item, v))
            continue
        if v.honoured:
            res.honoured.append((item, v))
        if accept:
            bdir = roots["USER_CLAUDE"] / "backups" / "ccs-merge"
            _write_back(item, merged, bdir, live_only=item.base_supplied)
            res.backup_dir = bdir
            if item.base_supplied:
                res.adopted.append(item)
        res.resolved.append(item)
    return res


INJECT_WINDOW_WAIT_S = 20.0     # how long a launched tool may take to show its window
INJECT_POLL_S = 0.5


def _ask_inject_on_console(item: MergeItem, tool: str) -> bool:
    """Default `confirm_inject`: the announced focus-steal, then a question.
    Answered on a console only; anything else is a no -- a keystroke aimed
    at a pane nobody can see is exactly what must never happen."""
    _print_inject_warning(item, tool)
    if not _console_attached(sys.stdin):
        print("  (no console to answer on -- not injecting; the file stays closed)")
        return False
    try:
        return input("  paint your prior work into the tool now? [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def _print_inject_warning(item: MergeItem, tool: str) -> None:
    print(f"{item.label}: ccs will open {tool} and take the keyboard for about a "
          f"second to paint your prior work into its output pane.")
    print("  Hands off the keyboard and mouse until the merge window shows your "
          "text; anything typed meanwhile lands in the pane.")


def _inject_flow(res: MergeResult, item: MergeItem, merged: Path, base: Path,
                 tool: str, pname: str, profile: dict, inject_mode: str,
                 confirm_inject, ws: Path) -> int | None:
    """Reopen a resumed file in a tool that regenerates its output pane, and
    paint the person's work back in. Returns the tool's exit code, or None
    when nothing was launched.

    The order is the safety property, not a convenience:
      1. refuse before launching if the tool already shows this file -- a
         relaunch of an open file creates a HIDDEN second session whose
         close-time save prompt overwrites the work (measured, p1)
      2. warn, then consent (a declined prompt launches nothing)
      3. snapshot the work to a sidecar, launch, locate, paint, verify by
         reading the saved file
      4. in `finally` -- so Ctrl+C is covered too -- if the paint was not
         verified and the file changed, put the sidecar back. This runs
         BEFORE validate(): a cleanly regenerated pane can pass validation
         and would otherwise be installed as the person's resolution.
    """
    def refuse(reason: str) -> None:
        res.inject_refused.append((item, reason))

    usable, why = inject.available()
    if not usable:
        refuse(why)
        return None
    if inject_mode == "never":
        refuse("merge_inject is 'never' -- set it to 'ask' or 'always' to allow injection")
        return None
    snap = inject.snapshot(merged.name, profile)
    if not snap.get("ok"):
        refuse(f"could not inspect the tool's windows: {snap.get('reason', 'no reason')}")
        return None
    if snap.get("open"):
        refuse(f"{tool} already has {merged.name} open -- close that tab first, "
               f"then run again (relaunching an open file creates a hidden second "
               f"session whose save prompt would overwrite your work)")
        return None
    if inject_mode == "always":
        _print_inject_warning(item, tool)
    else:
        ask = confirm_inject or _ask_inject_on_console
        if not ask(item, tool):
            refuse("declined at the prompt -- the file stays closed; "
                   "`--relaunch --discard` reopens it without your edits")
            return None

    import json as _json
    before = ws / (merged.name + ".before.json")
    before.write_text(_json.dumps(snap), encoding="utf-8")
    sidecar = ws / (merged.name + ".pre-inject")
    kept = merged.read_bytes()
    sidecar.write_bytes(kept)

    verified = False
    rc: int | None = None
    proc = _spawn(tool, item, merged, base)
    try:
        located = None
        deadline = time.monotonic() + INJECT_WINDOW_WAIT_S
        while time.monotonic() < deadline:
            probe = inject.locate(merged.name, before, profile)
            if probe.get("ok"):
                located = probe
                break
            if proc.poll() is not None:            # the tool exited before showing a window
                break
            time.sleep(INJECT_POLL_S)
        if located is None:
            res.inject_failed.append((item, "the tool's output pane could not be located "
                                            "after launch -- it is showing a regenerated merge; "
                                            "close it WITHOUT saving"))
        else:
            result = inject.inject(merged.name, before, sidecar, merged, profile)
            verified = bool(result.get("ok"))
            if verified:
                res.injected.append(item)
            else:
                res.inject_failed.append((item, f"{result.get('reason', 'not verified')} -- the tool "
                                                f"is showing a regenerated merge; close it WITHOUT "
                                                f"saving, or ccs will put your work back"))
        rc = _wait_for_tool(proc)
    finally:
        if not verified and merged.exists() and merged.read_bytes() != kept:
            merged.write_bytes(kept)
            res.restored.append(item)
        for p in (sidecar, before):
            try:
                p.unlink()
            except OSError:
                pass
    return rc


def _ask_loss_on_console(item: MergeItem, v: ValidationResult) -> bool:
    """Default `confirm_loss`: show what the merged result drops, say what
    each answer does, ask, default no. Non-interactive (CI, piped stdin)
    never accepts -- a silent yes here is exactly the class of failure the
    gate exists to stop.

    Written to be READ (2026-09-03, after the maintainer met it on a real
    merge): the file first; what ccs knows and does not; a count instead of
    "(1)"; "your live file" instead of "ours"; every line cut to the terminal
    and the cut marked, with the command that shows it whole; and the two
    sentences the old prompt never had -- what `y` means and what `N` means.
    """
    if not interactive():
        return False
    # Colour roles follow render.py's family convention (csb's search_render):
    # bold cyan for the path, yellow for what needs the person's attention,
    # magenta for the file content at risk (what the CLI already uses for a
    # file that would be clobbered), dim for notes, bold for what you type.
    # render.c() is plain text when colour is off -- piped, CI, --no-color.
    c = render.c
    print(f"{c('bold_cyan', item.label)}: no common ancestor, so ccs cannot tell "
          f"a deliberate deletion from a lost line.")
    cut = False
    total = 0
    for side, lines in v.lost.items():
        whose = "your live file" if side == "ours" else "the payload's copy"
        n = len(lines)
        total += n
        print("  " + c("yellow", f"The merged result drops {n} line{'' if n == 1 else 's'} "
                                 f"that {whose} has:"))
        for ln in lines[:12]:
            shown, was_cut = render.fit(ln, indent=4)
            cut = cut or was_cut
            print(f"    {c('magenta', shown)}")
        if n > 12:
            print(c("dim", f"    ... and {n - 12} more"))
    if cut:
        print(c("dim", "  (cut to fit the terminal; ") + c("bold", f"ccs diff {item.label}")
              + c("dim", " shows them whole)"))
    # The closing pair is SIDE-AWARE (found by the maintainer's eyes step,
    # 2026-09-03: a world dropping one line from each side read "if the
    # payload removed those lines" over a line that was his own). Whose
    # line it was decides who could have meant to drop it.
    sides = {s for s, lines in v.lost.items() if lines}
    those = "that line" if total == 1 else "those lines"
    they = "it" if total == 1 else "they"
    if sides == {"ours"}:
        first = f"If you dropped {those} from your live file on purpose, install the result: "
        second = f"If {they} should have stayed, answer "
    elif sides == {"theirs"}:
        first = f"If the payload removed {those} on purpose, install the result: "
        second = f"If {they} should have stayed, answer "
    else:
        first = ("If each of those lines was dropped on purpose -- the payload's by the "
                 "payload, yours by you -- install the result: ")
        second = "If any of them should have stayed, answer "
    print(f"  {first}{c('bold', 'y')}.")
    try:
        answer = input(f"  {second}{c('bold', 'N')} and resolve the file by hand.  [y/N] ")
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in ("y", "yes")


def _excerpt(line: str, n: int) -> str:
    """The first `n` characters of a line for a failure message, with the cut
    MARKED. These strings are data (they travel in `failures` and print later,
    indented, in red), so the budget is fixed rather than the terminal's --
    but a silent cut is never right: the person reading `(first: '...')` must
    be able to tell a short line from a shortened one."""
    return line if len(line) <= n else line[:n - 3] + "..."


def _side_name(side: str) -> str:
    """'ours'/'theirs' as a person reads them. Same words as the no-base
    prompt, so the prompt and the failure that follows it agree."""
    return "your live file" if side == "ours" else "the payload's copy"


def _dominant_eol(blob: bytes) -> bytes:
    """Whichever line ending the file mostly uses. Preserved on write-back."""
    crlf = blob.count(b"\r\n")
    return b"\r\n" if crlf > (blob.count(b"\n") - crlf) else b"\n"


def _write_back(item: MergeItem, merged: Path,
                backup_dir: Path | None = None, live_only: bool = False) -> None:
    """Install a VALIDATED merge on both sides. Never called before validate().

    `live_only` is the ADOPTION case (a supplied base): the checkout stays at
    HEAD. Installing a keep box's merge into the checkout would publish its
    own sections to every other box and make them the next inferred base --
    the exact mechanism that deletes them on the following merge.

    Writes to `repo_dest`, NOT `repo`. On the HEAD axis `repo` is the staged
    copy of theirs inside the merge workspace, so writing there installed
    nothing into the payload repo and destroyed the staged original. The live
    tree got the merge, the checkout did not, and `--accept` reported success.

    The merge runs on LF-normalised copies, so the result is restored to the
    line-ending style the LIVE file already used -- rewriting a 966-line file
    from CRLF to LF would show up as a whole-file change in every later diff.
    """
    dest_repo = item.repo_dest or item.repo
    blob = _normalize_eol(merged.read_bytes())
    eol = _dominant_eol(item.live.read_bytes()) if item.live.is_file() else b"\n"
    if eol == b"\r\n":
        blob = blob.replace(b"\n", b"\r\n")


    # Back up BOTH originals BEFORE touching either. A merge is the one
    # operation that writes two trees at once, so a bad result costs twice as
    # much to undo. Raise rather than proceed: a silent backup failure while
    # reporting "originals backed up" is the defect this module exists to stop.
    if backup_dir is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        safe = item.label.replace("/", "__").replace("\\", "__")
        for role, src in (("live", item.live), ("repo", dest_repo)):
            if src.is_file():
                (backup_dir / f"{safe}.{role}.bak").write_bytes(src.read_bytes())

    item.live.parent.mkdir(parents=True, exist_ok=True)
    item.live.write_bytes(blob)
    if live_only:
        return
    dest_repo.parent.mkdir(parents=True, exist_ok=True)
    dest_repo.write_bytes(blob)


def _console_attached(stream) -> bool:
    """True only when `stream` is a REAL console.

    ``isatty()`` is NOT sufficient on Windows. Git Bash's ``/dev/null`` maps to
    ``NUL``, which *is* a character device, so ``sys.stdout.isatty()`` returns
    **True** under ``> /dev/null`` -- the exact redirection scripts and CI use.
    Measured on this platform:

        >/dev/null :  isatty()=True   GetConsoleMode()=False
        | pipe     :  isatty()=False  GetConsoleMode()=False

    This bug launched three BeyondCompare windows from a redirected command
    and hung it until the processes were killed, which is precisely the
    failure the guard exists to prevent. GetConsoleMode() succeeds only for a
    genuine console handle, so it separates the two cases correctly.
    """
    try:
        import ctypes
        import msvcrt
        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = ctypes.c_ulong()
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle,
                                                          ctypes.byref(mode)))
    except Exception:
        # Non-Windows, or a stream with no real fd: isatty is honest there.
        try:
            return stream.isatty()
        except Exception:
            return False


# Set by CI systems; an interactive tool must never open in one.
_CI_VARS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL",
            "BUILDKITE", "TEAMCITY_VERSION", "TF_BUILD")


def interactive() -> bool:
    """Whether it is safe to open a GUI/TUI and wait for a human."""
    if os.environ.get("CCS_NO_LAUNCH"):
        return False
    if any(os.environ.get(v) for v in _CI_VARS):
        return False
    return _console_attached(sys.stdout)


def launch(tool: str, item: MergeItem, merged: Path, base: Path,
           wait: bool = True) -> int:
    """Run the user's tool through git's own mergetool contract and, unless
    `wait` is False, return its exit code.

    Never launches anything when stdout is not a TTY: a GUI on a CI runner
    hangs forever, and silently picking a side is precisely the failure this
    module exists to prevent (AC-7).
    """
    proc = _spawn(tool, item, merged, base)
    if not wait:
        # Preview is "open it so I can look" -- there is nothing to come back
        # for, so blocking the shell until the user closes a GUI is pure
        # friction. Return immediately and leave the tool running.
        return 0
    return _wait_for_tool(proc)


def _spawn(tool: str, item: MergeItem, merged: Path, base: Path) -> subprocess.Popen:
    """Start the tool and return the process; `_wait_for_tool` collects it.
    Split from launch() so the injection flow can act while the tool is
    open."""
    if not interactive():
        raise MergeError(
            "no console attached -- refusing to launch an interactive merge tool "
            "(resolve manually, or run where a terminal is attached)")
    cmd = tool_command(tool)
    if not cmd:
        raise MergeError(f"no mergetool.{tool}.cmd configured, and '{tool}' is not a "
                         f"built-in ({', '.join(BUILTIN_TOOLS)})")
    line = substitute(cmd, item, merged, base)
    # env is still exported so a tool that reads the variables directly works,
    # but correctness must NOT depend on it -- see substitute().
    env = {**os.environ, **_placeholders(item, merged, base)}
    # CREATE_NEW_PROCESS_GROUP puts the tool in its own group so a console
    # Ctrl+C reaches ccs INSTEAD of being swallowed by the child, and so
    # killing ccs never takes the tool down with it.
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(line, shell=True, env=env, creationflags=flags)


def _wait_for_tool(proc: subprocess.Popen) -> int:
    """Wait for a launched tool, interruptibly, and return its exit code."""
    try:
        # POLL, do not block. On Windows `Popen.wait()` sits in
        # WaitForSingleObject with an infinite timeout, and Python cannot
        # deliver KeyboardInterrupt until it returns -- so Ctrl+C appeared to
        # do nothing and only took effect once the diff tool was closed, which
        # is precisely when it was no longer wanted. A short sleep loop is
        # interruptible, so Ctrl+C lands immediately.
        while True:
            rc = proc.poll()
            if rc is not None:
                return rc
            time.sleep(0.15)
    except KeyboardInterrupt:
        # Leave the tool OPEN. The user interrupted ccs, not their editor, and
        # an unsaved merge in a GUI is exactly the thing not to destroy.
        # An earlier version killed the whole process tree here, which closed
        # BeyondCompare -- the opposite of what was wanted.
        raise MergeError(
            "cancelled -- your diff tool is still open and untouched; "
            "nothing was validated or installed. Re-run when you have saved, "
            "and your edits will be picked up (reported as `resumed`).")


def _placeholders(item: MergeItem, merged: Path, base: Path) -> dict[str, str]:
    return {"LOCAL": str(item.live), "REMOTE": str(item.repo),
            "BASE": str(base), "MERGED": str(merged)}


def substitute(cmd: str, item: MergeItem, merged: Path, base: Path) -> str:
    """Expand $LOCAL/$BASE/$REMOTE/$MERGED in a mergetool command ourselves.

    Exporting them as environment variables is NOT enough on Windows.
    ``subprocess.run(shell=True)`` runs the string through **cmd.exe**, which
    expands ``%VAR%`` and leaves ``$VAR`` untouched -- so Beyond Compare
    received four files literally named ``$REMOTE``, ``$BASE``, ``$LOCAL`` and
    ``$MERGED`` and reported "File Not Found" for every pane.

    git itself gets away with the env-only approach because it runs the command
    through ``sh``. ccs does not depend on a POSIX shell being present, so it
    performs the substitution directly. Both ``$NAME`` and ``${NAME}`` forms are
    handled; longest names first so ``$MERGED`` is never truncated by a shorter
    prefix match.
    """
    for name, value in sorted(_placeholders(item, merged, base).items(),
                              key=lambda kv: -len(kv[0])):
        cmd = cmd.replace("${" + name + "}", value).replace("$" + name, value)
    return cmd
