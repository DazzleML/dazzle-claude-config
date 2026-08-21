"""Three-way merge orchestration -- ccs drives git and the user's diff tool.

**ccs implements no merge engine and keeps no tool registry.** ``git merge-file``
does the 3-way; ``git mergetool`` already drives ~20 tools through the
``$LOCAL $BASE $REMOTE $MERGED`` contract. What ccs adds is the part git cannot
know by itself: which manifest entries may be merged at all, and whether the
result actually kept both sides' content.

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

from .manifest import Entry, Manifest
from .secrets import is_denied, scan_file
from .syncmap import EntryDiff, _normalize_eol, diff_all

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

    @property
    def ok(self) -> bool:
        return not self.failures


def plan(manifest: Manifest, checkout: Path, roots: dict[str, Path], *,
         theirs_from: str = "head", stage: Path | None = None,
         base_mode: str = "auto") -> list[MergeItem]:
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
        items.extend(_head_items(manifest, checkout, roots, items, stage, base_mode))
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
                base_mode: str = "auto") -> list[MergeItem]:
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
    """Best-effort ancestor: the committed version closest to OURS.

    Phase 1 has no recorded ancestry, so the base is estimated rather than
    known. Two guards make a wrong estimate safe:

      * HEAD is excluded outright -- HEAD *is* theirs, and base==theirs makes
        git conclude "they changed nothing, take ours", silently discarding
        the other side (measured: 56 lines).
      * A candidate equal to either side is rejected for the same reason in
        mirror image (base==ours -> "take theirs", measured: 7 refs lost).

    Returning None is a valid, honest answer: no usable base, degrade to a
    2-way hand-off rather than invent a third input.
    """
    norm = _normalize_eol
    ours_n, theirs_n = norm(ours), norm(theirs)
    rc, out = _git(["log", "--format=%H", "--follow", "--", repo_path], cwd=checkout)
    if rc != 0 or not out:
        return None
    best: tuple[int, bytes, str] | None = None
    for sha in out.split()[1:max_commits + 1]:      # [1:] skips HEAD == theirs
        p = subprocess.run(["git", "show", f"{sha}:{repo_path}"],
                           cwd=str(checkout), capture_output=True)
        if p.returncode != 0 or not p.stdout:
            continue
        cand = norm(p.stdout)
        if cand == ours_n or cand == theirs_n:
            continue                                 # degenerate: would collapse
        ratio, n = base_phantom_ratio(cand, ours_n, theirs_n)
        if n >= PHANTOM_MIN_LINES and ratio >= PHANTOM_RATIO:
            # Sibling, not ancestor -- using it would fabricate deletions. Keep
            # it anyway: it is still the nearest historical version, and being
            # able to LOOK at it answers "what changed since the last release
            # of this file" without ever being fed to the merge as a base.
            if rejected is not None:
                rejected.append((p.stdout, sha[:7], n, ratio))
            continue
        sm = difflib.SequenceMatcher(
            None, cand.decode("utf-8", "replace").splitlines(),
            ours_n.decode("utf-8", "replace").splitlines(), autojunk=False)
        score = sum((i2 - i1) + (j2 - j1)
                    for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal")
        if best is None or score < best[0]:
            best = (score, p.stdout, sha[:7])
    return (best[1], best[2]) if best else None


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


def _tool_usable(name: str) -> bool:
    """A configured tool name is only usable if its binary actually exists.

    Measured: `merge.tool = bc` resolves to a bare `BCompare.exe` that is NOT
    on PATH, while `diff.tool = bc4` points at a real absolute path. Trusting
    the configured name would fail on the very machine that configured it, so
    the name is never trusted without a probe (AC-5).
    """
    rc, cmd = _git(["config", "--get", f"mergetool.{name}.cmd"])
    if rc != 0 or not cmd:
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
            raise MergeError(
                f"merge tool '{explicit}' is configured but its binary was not "
                f"found; check `git config mergetool.{explicit}.cmd`")
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

    for name in candidates:
        if _tool_usable(name):
            return name
    raise MergeError(
        "no usable merge tool found -- configure one, e.g.\n"
        '  git config --global mergetool.bc4.cmd '
        "'\"C:/path/to/bcomp.exe\" --wait \"$LOCAL\" \"$REMOTE\" \"$BASE\" \"$MERGED\"'")


# --------------------------------------------------------------------------
# Seeding and validation
# --------------------------------------------------------------------------

def seed(item: MergeItem, merged: Path, union: bool = False) -> int:
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
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                None, src_lines, res_lines, autojunk=False).get_opcodes():
            if tag == "delete":
                dropped += [l for l in src_lines[i1:i2] if l.strip()]
            elif tag == "replace":
                # A replacement may be a REWRITE of the same statement, or
                # unrelated text that merely aligned there. Similarity tells
                # them apart: "Two of these are re-entrant..." against "Three
                # of these are re-entrant..." is a rewrite; "THEIRS-SECTION-A"
                # against "OURS-ONLY" is a clobber. Only clobbers are loss.
                gone, came = src_lines[i1:i2], res_lines[j1:j2]
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

    known = _lines(item.live) | _lines(item.repo) | _lines(item.base)
    if known:
        invented = [ln for ln in {l.strip() for l in text.splitlines() if l.strip()}
                    if ln not in known and not ln.startswith(CONFLICT_MARKERS)]
        if invented:
            res.failures.append(
                f"{len(invented)} line(s) in the result appear in neither side "
                f"nor the base (first: {invented[0][:60]!r})")

    # --union keeps BOTH sides, so its characteristic failure is duplication:
    # a paragraph present in ours and theirs can land twice. Only substantial
    # lines are checked -- blanks, fences and short list markers legitimately
    # repeat throughout a document.
    merged_lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 40]
    from collections import Counter
    mc = Counter(merged_lines)
    oc = Counter(l.strip() for l in _text_of(item.live).splitlines() if len(l.strip()) > 40)
    tc = Counter(l.strip() for l in _text_of(item.repo).splitlines() if len(l.strip()) > 40)
    dupes = [l for l, n in mc.items() if n > max(oc.get(l, 0), tc.get(l, 0))]
    if dupes:
        res.failures.append(
            f"{len(dupes)} line(s) appear more often than on either side -- "
            f"content was duplicated (first: {dupes[0][:60]!r})")

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


def two_way_labels(manifest: Manifest, checkout: Path,
                   roots: dict[str, Path]) -> list[str]:
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
    for d in diff_all(manifest, checkout, roots):
        if d.mismatch:
            continue
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
        union: bool = False, launch_tool: bool = True,
        preview: bool = False, base_mode: str = "auto") -> MergeResult:
    """Plan, seed, validate and (optionally) hand off each divergent file.

    Nothing is written back unless `accept` is set AND validation passed. The
    workspace survives either way so a run can be inspected, re-run, or thrown
    away without touching the live tree.
    """
    res = MergeResult()
    ws = workspace_for(roots)
    ws.mkdir(parents=True, exist_ok=True)
    res.workspace = ws

    items = plan(manifest, checkout, roots, stage=ws, base_mode=base_mode)
    if only:
        items = [i for i in items if i.entry.repo.startswith(only)]

    res.refused = [i for i in items if not i.mergeable]
    mergeable = [i for i in items if i.mergeable]
    if dry_run:
        res.planned = mergeable
        return res
    if not mergeable:
        return res
    resolved_tool = resolve_tool(tool) if (not dry_run and launch_tool) else None

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
            seed(item, merged, union=union)   # absent, or untouched since seeding
            stamp.write_bytes(merged.read_bytes())
        else:
            res.resumed.append(item)          # human edits present: keep them
        if launch_tool:
            launch(resolved_tool, item, merged, item.base or empty,
                   wait=not preview)
        if preview:
            # Look, do not decide. Nothing is validated and nothing is
            # installed -- this exists so a user can SEE the three sides in
            # their own tool before committing to a resolution.
            res.previewed.append(item)
            continue
        v = validate(item, merged, probes=probes)
        if not v.ok:
            res.unresolved.append((item, v))
            continue
        if accept:
            bdir = roots["USER_CLAUDE"] / "backups" / "ccs-merge"
            _write_back(item, merged, bdir)
            res.backup_dir = bdir
        res.resolved.append(item)
    return res


def _dominant_eol(blob: bytes) -> bytes:
    """Whichever line ending the file mostly uses. Preserved on write-back."""
    crlf = blob.count(b"\r\n")
    return b"\r\n" if crlf > (blob.count(b"\n") - crlf) else b"\n"


def _write_back(item: MergeItem, merged: Path,
                backup_dir: Path | None = None) -> None:
    """Install a VALIDATED merge on both sides. Never called before validate().

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
    dest_repo.parent.mkdir(parents=True, exist_ok=True)
    item.live.write_bytes(blob)
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
    """Run the user's tool through git's own mergetool contract.

    Never launches anything when stdout is not a TTY: a GUI on a CI runner
    hangs forever, and silently picking a side is precisely the failure this
    module exists to prevent (AC-7).
    """
    if not interactive():
        raise MergeError(
            "no console attached -- refusing to launch an interactive merge tool "
            "(resolve manually, or run where a terminal is attached)")
    rc, cmd = _git(["config", "--get", f"mergetool.{tool}.cmd"])
    if rc != 0 or not cmd:
        raise MergeError(f"no mergetool.{tool}.cmd configured")
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
    proc = subprocess.Popen(line, shell=True, env=env, creationflags=flags)

    if not wait:
        # Preview is "open it so I can look" -- there is nothing to come back
        # for, so blocking the shell until the user closes a GUI is pure
        # friction. Return immediately and leave the tool running.
        return 0

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
