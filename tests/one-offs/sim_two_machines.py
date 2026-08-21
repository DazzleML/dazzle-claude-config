"""sim_two_machines.py -- empirical two-machine simulator for ccs (dazzle-claude-config).

PURPOSE
-------
Builds the five-node model from the project README (L1 laptop-live -> C1
laptop-checkout -> R bare "GitHub" repo -> C2 desktop-checkout -> L2
desktop-live, mirrored the other direction too) out of REAL git repos and
REAL files under a scratch directory, drives them through a matrix of
sync scenarios, and at each interesting point asks THREE questions,
independently of each other:

  1. TRUTH   -- computed from git log + file bytes alone, never from ccs.
                Is a path's live copy one-sided (checkout ahead / live
                ahead), genuinely two-sided, or untracked?
  2. CLAIM   -- what ccs's own machinery says: the STATUS bucket
                (modified / live-only / repo-only, from syncmap.diff_all,
                which is what `ccs status`'s text is built from) and the
                GUARD verdict (merge.two_way_labels + merge.infer_base,
                which is what actually blocks `collect`/`apply`).
  3. AGREE?  -- does the guard's claim match the ground truth, and if not,
                which way is it wrong (false-both / false-one-sided /
                silent-overwrite-risk)?

This is run under THREE variants of the tool source, side by side (batch 2
added the third after the exhaustive-scenario-space DWP showed naive S1
fails SC-14, the "revert trap"):

  pristine       -- the tool repo's dazzle_claude_config/ as committed
                    (0.3.1-era `infer_base`: a historical candidate equal
                    to either side is discarded as "degenerate").
  s1_corrected   -- (aka "naive S1", V2) the scratch copy with
                    `infer_base`'s two-line equality skip replaced by an
                    early `return (p.stdout, sha[:7])` for equality to
                    EITHER side, per the DWP's Stage 4 build step 1 /
                    Addendum 2 correction: equality is accepted BEFORE the
                    phantom-deletion check and BEFORE scoring. Batch 2
                    shows this is still wrong for SC-14 (revert trap).
  refined        -- (V3) the scenario-space DWP's Stage 4a rule: equality
                    to OURS still returns immediately (distance zero,
                    nothing nearer exists). Equality to THEIRS is EXEMPT
                    from the phantom check (which is meaningless when
                    base==theirs) but does NOT short-circuit -- it competes
                    on ordinary distance-to-ours scoring like any other
                    candidate. HEAD is skipped by comparing shas to
                    `git rev-parse HEAD`, not by list position (`[1:]`),
                    since batch 1 found `--follow` can elide a merge
                    commit from the list entirely.

All variants are imported from throwaway copies of the package under a
scratch directory (never the pip-installed editable package, never the
real ~/.claude or any real checkout) -- see `_prepare_variants()`. TW1 (the
bug where `two_way_labels` skips directory-shaped entries outright) is
NOT fixed here; every scenario carries a file entry AND a directory entry
so TW1's absence is visible in the output rather than fixed.

HOW TO RUN
----------
    python tests/one-offs/sim_two_machines.py                  # run everything
    python tests/one-offs/sim_two_machines.py --scenario B      # just one letter
    python tests/one-offs/sim_two_machines.py --json out.json   # dump machine-
                                                                 # readable results

Requires only git on PATH and the stdlib (the package itself has zero
third-party dependencies). Nothing here writes outside a fresh
tempfile.mkdtemp() scratch tree; nothing here touches ~/.claude, ~/claude,
or the real dazzle-claude-code-config-personal checkout.

ADDING A SCENARIO
------------------
Scenarios are plain functions `scenario_X(world) -> list[dict]` registered
in SCENARIOS at the bottom. A minimal one is ~10 lines:

    def scenario_Z(world):
        events = []
        edit_live(world.laptop, TARGET_F, b"...\n")
        collect(world, "laptop")
        commit(world, "laptop", "edit F")
        push(world, "laptop")
        pull(world, "desktop")
        events.append(snapshot(world, "desktop", "after pull, before apply"))
        return events
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Constants: manifest shape, seed content, git identity
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_SRC = REPO_ROOT / "dazzle_claude_config"
CONFTEST_SRC = REPO_ROOT / "tests" / "conftest.py"

GIT_ID = ["-c", "user.email=ccs-sim@test.invalid", "-c", "user.name=ccs-sim",
          "-c", "commit.gpgsign=false", "-c", "pull.rebase=false"]

MANIFEST_JSON = {
    "manifest_version": 1,
    "territories": {"dotclaude": {"root_var": "CLAUDE_DIR", "repo_dir": "dotclaude"}},
    "entries": [
        {"repo": "dotclaude/CLAUDE.md", "territory": "dotclaude",
         "target": "CLAUDE.md", "strategy": "copy"},
        {"repo": "dotclaude/skills", "territory": "dotclaude",
         "target": "skills", "strategy": "copy"},
    ],
    "collect_exclude": [],
    "deny": [],
}

# The file entry, and the one file that lives inside the directory entry.
# Every scenario exercises both, so TW1 (directory entries invisible to
# two_way_labels) shows up everywhere rather than needing its own scenario.
F_REPO, F_TARGET = "dotclaude/CLAUDE.md", "CLAUDE.md"
S_REPO, S_TARGET = "dotclaude/skills/S.md", "skills/S.md"

SEED_F = b"line1\nline2\nline3\n"
SEED_S = b"skillA\nskillB\nskillC\n"

# Strictly-increasing commit timestamps so `git log` ordering never depends
# on wall-clock resolution (Windows commits inside one test run routinely
# land in the same second, which would otherwise make history order, and
# therefore which candidate infer_base sees first, nondeterministic).
_CLOCK = {"t": 1_700_000_000}


def _tick() -> int:
    _CLOCK["t"] += 60
    return _CLOCK["t"]


# --------------------------------------------------------------------------
# Variant loading: two throwaway copies of the package, imported under
# distinct names so both can be live in the same process at once.
# --------------------------------------------------------------------------

def _make_s1_corrected(merge_py_text: str) -> str:
    """Apply the DWP's Stage-4/Addendum-2 fix to a copy of merge.py's text.

    Exact transform: the two-line equality skip inside infer_base's loop
    becomes an early return, BEFORE the phantom-ratio check -- so equality
    to either side short-circuits phantom reasoning and scoring entirely,
    per Addendum 2 ("S1 as first written was direction-asymmetric").
    """
    original = (
        "        cand = norm(p.stdout)\n"
        "        if cand == ours_n or cand == theirs_n:\n"
        "            continue                                 # degenerate: would collapse\n"
    )
    corrected = (
        "        cand = norm(p.stdout)\n"
        "        if cand == ours_n or cand == theirs_n:\n"
        "            return (p.stdout, sha[:7])                # S1: equality IS ancestry proof\n"
    )
    n = merge_py_text.count(original)
    if n != 1:
        raise RuntimeError(
            f"S1 patch: expected exactly 1 occurrence of the equality-skip "
            f"snippet in merge.py, found {n}. merge.py has drifted from the "
            f"text this script was written against -- update _make_s1_corrected.")
    return merge_py_text.replace(original, corrected)


# Verbatim original infer_base function body (84026c8), used as the match
# target for the V3 refined-rule patch. Matched as one block (rather than a
# small fragment, as V2 does) because V3's change touches the loop's control
# flow in more than one place: the HEAD-skip mechanism, the equality branch,
# and where the phantom check applies.
_ORIGINAL_INFER_BASE = '''def infer_base(checkout: Path, repo_path: str, ours: bytes, theirs: bytes,
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
    return (best[1], best[2]) if best else None'''

_REFINED_INFER_BASE = '''def infer_base(checkout: Path, repo_path: str, ours: bytes, theirs: bytes,
               max_commits: int = 25,
               rejected: list | None = None) -> tuple[bytes, str] | None:
    """Best-effort ancestor: the committed version closest to OURS.

    V3 REFINED RULE (exhaustive-scenario-space DWP, Stage 4a): the naive
    "equality to either side returns immediately" rule (V2/S1) is wrong for
    SC-14, the revert trap -- a candidate equal to THEIRS is not proof theirs
    is unchanged since the real sync point P, because a revert can make HEAD
    equal an OLDER commit while the checkout genuinely moved past P in
    between. So:

      * Equality to OURS is still a distance-zero candidate -- live has no
        unique content relative to it, nothing can be nearer. Return
        immediately.
      * Equality to THEIRS is exempt from the phantom check (meaningless
        when base==theirs: every deletion is "retained" by construction),
        but does NOT short-circuit -- it competes on ordinary
        distance-to-ours scoring like any other candidate, and only wins if
        it is nearest.
      * HEAD is skipped by SHA identity (`git rev-parse HEAD`), not by list
        position (`[1:]`) -- batch 1 of the simulator found `--follow` can
        elide a merge commit from the log entirely, which makes position 0
        an unsafe proxy for "this is HEAD."
    """
    norm = _normalize_eol
    ours_n, theirs_n = norm(ours), norm(theirs)
    rc, out = _git(["log", "--format=%H", "--follow", "--", repo_path], cwd=checkout)
    if rc != 0 or not out:
        return None
    rc_head, head_sha = _git(["rev-parse", "HEAD"], cwd=checkout)
    best: tuple[int, bytes, str] | None = None
    considered = 0
    for sha in out.split():
        if rc_head == 0 and sha == head_sha:
            continue                                 # skip HEAD by identity
        if considered >= max_commits:
            break
        considered += 1
        p = subprocess.run(["git", "show", f"{sha}:{repo_path}"],
                           cwd=str(checkout), capture_output=True)
        if p.returncode != 0 or not p.stdout:
            continue
        cand = norm(p.stdout)
        if cand == ours_n:
            return (p.stdout, sha[:7])                # distance zero: nothing nearer exists
        if cand != theirs_n:
            ratio, n = base_phantom_ratio(cand, ours_n, theirs_n)
            if n >= PHANTOM_MIN_LINES and ratio >= PHANTOM_RATIO:
                if rejected is not None:
                    rejected.append((p.stdout, sha[:7], n, ratio))
                continue
        # cand == theirs_n falls through here WITHOUT the phantom check --
        # exempt, not rejected -- and competes on score like any candidate.
        sm = difflib.SequenceMatcher(
            None, cand.decode("utf-8", "replace").splitlines(),
            ours_n.decode("utf-8", "replace").splitlines(), autojunk=False)
        score = sum((i2 - i1) + (j2 - j1)
                    for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal")
        if best is None or score < best[0]:
            best = (score, p.stdout, sha[:7])
    return (best[1], best[2]) if best else None'''


def _make_v3_refined(merge_py_text: str) -> str:
    """Apply the exhaustive-scenario-space DWP's Stage 4a refined rule to a
    copy of merge.py's text. See _REFINED_INFER_BASE's docstring for the
    exact rule; matched and replaced as one whole-function block since the
    change touches the HEAD-skip mechanism as well as the equality branch."""
    n = merge_py_text.count(_ORIGINAL_INFER_BASE)
    if n != 1:
        raise RuntimeError(
            f"V3 patch: expected exactly 1 occurrence of the original "
            f"infer_base body in merge.py, found {n}. merge.py has drifted "
            f"from the text this script was written against -- update "
            f"_ORIGINAL_INFER_BASE/_REFINED_INFER_BASE.")
    return merge_py_text.replace(_ORIGINAL_INFER_BASE, _REFINED_INFER_BASE)


# V4 -- the exhaustive-enumeration rule (scenario-space DWP, Addendum 2).
# HEAD is INCLUDED as an ordinary candidate (nothing excluded by position or
# sha identity). Every non-ours-equal candidate is SCORED regardless of
# whether the phantom check would reject it; the base is the GLOBALLY
# nearest candidate by distance -- but if that globally-nearest candidate is
# one the phantom check rejected, infer_base refuses outright (None) rather
# than silently falling back to some farther, unrejected candidate that
# would just be a worse guess wearing a green light.
_V4_INFER_BASE = '''def infer_base(checkout: Path, repo_path: str, ours: bytes, theirs: bytes,
               max_commits: int = 25,
               rejected: list | None = None) -> tuple[bytes, str] | None:
    """Best-effort ancestor: the committed version closest to OURS.

    V4 -- the exhaustive-enumeration rule (scenario-space DWP, Addendum 2).
    V3 still excluded HEAD from candidacy (by sha identity) on the theory
    that HEAD-as-base risks the "56 lines silently dropped" incident. But
    that incident was about a base being WRONGLY set to HEAD without first
    checking it explains ours; when HEAD genuinely IS the sync point (the
    ordinary post-apply-then-edit case, SC-11a), excluding it leaves NO
    candidate that explains "theirs hasn't changed" at all, and infer_base
    falls back to an older, wrong commit -- a false two-sided refusal on
    what is very often the single most common real-world edit shape.

    So V4 includes HEAD as an ordinary candidate (nothing is excluded by
    position or sha), scores EVERY non-ours-equal candidate (rejected ones
    too), and picks the GLOBALLY nearest one -- but if that nearest
    candidate is one the phantom check rejected, infer_base refuses (None)
    outright rather than quietly substituting a farther, unrejected
    candidate, which would just be trading one wrong guess for another
    while looking safer than it is.
    """
    norm = _normalize_eol
    ours_n, theirs_n = norm(ours), norm(theirs)
    rc, out = _git(["log", "--format=%H", "--follow", "--", repo_path], cwd=checkout)
    if rc != 0 or not out:
        return None
    all_candidates: list[tuple[int, bytes, str, bool]] = []  # (score, blob, sha, rejected?)
    for sha in out.split()[:max_commits]:
        p = subprocess.run(["git", "show", f"{sha}:{repo_path}"],
                           cwd=str(checkout), capture_output=True)
        if p.returncode != 0 or not p.stdout:
            continue
        cand = norm(p.stdout)
        if cand == ours_n:
            return (p.stdout, sha[:7])                # distance zero
        is_rejected = False
        if cand != theirs_n:
            ratio, n = base_phantom_ratio(cand, ours_n, theirs_n)
            if n >= PHANTOM_MIN_LINES and ratio >= PHANTOM_RATIO:
                is_rejected = True
                if rejected is not None:
                    rejected.append((p.stdout, sha[:7], n, ratio))
        # Every candidate is scored, rejected or not -- the GLOBAL nearest
        # check below needs to see all of them, not just survivors.
        sm = difflib.SequenceMatcher(
            None, cand.decode("utf-8", "replace").splitlines(),
            ours_n.decode("utf-8", "replace").splitlines(), autojunk=False)
        score = sum((i2 - i1) + (j2 - j1)
                    for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal")
        all_candidates.append((score, p.stdout, sha[:7], is_rejected))
    if not all_candidates:
        return None
    nearest = min(all_candidates, key=lambda t: t[0])
    if nearest[3]:                                     # globally nearest was rejected
        return None                                    # refuse; never fall back farther
    return (nearest[1], nearest[2])'''


def _make_v4_full(merge_py_text: str) -> str:
    """Apply the exhaustive-enumeration V4 rule. See _V4_INFER_BASE's
    docstring for the exact rule."""
    n = merge_py_text.count(_ORIGINAL_INFER_BASE)
    if n != 1:
        raise RuntimeError(
            f"V4 patch: expected exactly 1 occurrence of the original "
            f"infer_base body in merge.py, found {n}. merge.py has drifted "
            f"from the text this script was written against -- update "
            f"_ORIGINAL_INFER_BASE/_V4_INFER_BASE.")
    return merge_py_text.replace(_ORIGINAL_INFER_BASE, _V4_INFER_BASE)


_VARIANT_PATCHERS = {
    "pristine": None,
    "s1_corrected": _make_s1_corrected,
    "refined": _make_v3_refined,
    "v4_full": _make_v4_full,
}


def _prepare_variants(scratch_root: Path) -> dict[str, types.ModuleType]:
    """Copy the package once per variant into scratch, patch, import all.

    Returns {"pristine": <namespace>, "s1_corrected": <namespace>,
    "refined": <namespace>}, each a SimpleNamespace-like module exposing
    .manifest .merge .syncmap .collect .apply as submodules, loaded from the
    SCRATCH COPY only -- never the pip-installed editable package. Verified
    by asserting each submodule's __file__ is under scratch_root.
    """
    if scratch_root.exists():
        shutil.rmtree(scratch_root)
    scratch_root.mkdir(parents=True)

    variants: dict[str, types.ModuleType] = {}
    for variant_name, patcher in _VARIANT_PATCHERS.items():
        vdir = scratch_root / variant_name
        pkg_dst = vdir / "dazzle_claude_config"
        shutil.copytree(PKG_SRC, pkg_dst,
                        ignore=shutil.ignore_patterns("__pycache__"))
        (vdir / "tests").mkdir(parents=True, exist_ok=True)
        if CONFTEST_SRC.is_file():
            shutil.copy2(CONFTEST_SRC, vdir / "tests" / "conftest.py")

        if patcher is not None:
            merge_py = pkg_dst / "merge.py"
            merge_py.write_text(patcher(merge_py.read_text(encoding="utf-8")),
                                encoding="utf-8")

        alias = f"dcc_{variant_name}"
        spec = importlib.util.spec_from_file_location(
            alias, pkg_dst / "__init__.py",
            submodule_search_locations=[str(pkg_dst)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[alias] = module
        spec.loader.exec_module(module)

        ns = types.SimpleNamespace(name=variant_name, root=vdir)
        for sub in ("manifest", "merge", "syncmap", "collect", "apply"):
            m = importlib.import_module(f"{alias}.{sub}")
            assert str(Path(m.__file__).resolve()).startswith(str(scratch_root.resolve())), (
                f"{alias}.{sub} loaded from outside scratch: {m.__file__}")
            setattr(ns, sub, m)
        variants[variant_name] = ns
    return variants


# --------------------------------------------------------------------------
# Git plumbing (independent of ccs; also used to build the fixtures)
# --------------------------------------------------------------------------

def _git(cwd: Path, *args: str, env: dict | None = None, check: bool = True):
    full_env = {**os.environ, **(env or {})}
    p = subprocess.run(["git", *GIT_ID, *args], cwd=str(cwd),
                       capture_output=True, text=False, env=full_env)
    if check and p.returncode != 0:
        raise RuntimeError(
            f"git {args} in {cwd} failed ({p.returncode}):\n"
            f"{p.stderr.decode('utf-8', 'replace')}")
    return p


def _git_text(cwd: Path, *args: str, check: bool = True) -> str:
    return _git(cwd, *args, check=check).stdout.decode("utf-8", "replace")


def git_show(checkout: Path, repo_path: str, sha: str = "HEAD") -> bytes | None:
    p = _git(checkout, "show", f"{sha}:{repo_path}", check=False)
    if p.returncode != 0 or not p.stdout:
        return None
    return p.stdout


def git_log_shas(checkout: Path, repo_path: str) -> list[str]:
    """Full SHAs, most-recent-first, [] if the path has no history at all."""
    p = _git(checkout, "log", "--format=%H", "--follow", "--", repo_path, check=False)
    if p.returncode != 0:
        return []
    out = p.stdout.decode("utf-8", "replace").strip()
    return out.split() if out else []


def git_is_ancestor(checkout: Path, sha: str, ref: str = "HEAD") -> bool:
    p = _git(checkout, "merge-base", "--is-ancestor", sha, ref, check=False)
    return p.returncode == 0


def _normalize_eol(blob: bytes) -> bytes:
    """Stand-alone copy -- deliberately NOT imported from the tool, so
    ground truth never depends on ccs's own code (even for something this
    small)."""
    return blob.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _init_repo(path: Path, bare: bool = False) -> None:
    path.mkdir(parents=True, exist_ok=True)
    args = ["init", "-q", "-b", "main"]
    if bare:
        args.insert(1, "--bare")
    _git(path, *args)


# --------------------------------------------------------------------------
# World model
# --------------------------------------------------------------------------

@dataclass
class Machine:
    name: str
    live: Path
    checkout: Path
    user_claude: Path


@dataclass
class World:
    tmpdir: Path
    bare: Path
    laptop: Machine
    desktop: Machine
    variants: dict[str, types.ModuleType]
    log: list[str] = field(default_factory=list)
    extra_machines: dict[str, Machine] = field(default_factory=dict)  # scenario O (3rd machine)
    # (machine_name, repo_path) -> full commit sha. THE thing ccs itself
    # cannot observe (the exhaustive-scenario-space DWP's "P"), tracked here
    # ONLY because the harness controls the whole world and can watch it
    # happen -- never inferred from content matching, which is exactly the
    # approximation SC-14 (the revert trap) breaks. Set by _record_sync_points,
    # called after every apply() and every non-empty commit() -- the two
    # moments the DWP's Stage 0.5 says P actually moves.
    sync_point: dict[tuple[str, str], str] = field(default_factory=dict)

    def machine(self, which: str) -> Machine:
        if which in self.extra_machines:
            return self.extra_machines[which]
        return self.laptop if which == "laptop" else self.desktop

    def note(self, msg: str) -> None:
        self.log.append(msg)


def _write_manifest(checkout: Path) -> None:
    (checkout / "ccs-manifest.json").write_text(
        json.dumps(MANIFEST_JSON, indent=1), encoding="utf-8")


def _seed_checkout(checkout: Path) -> None:
    (checkout / "dotclaude" / "skills").mkdir(parents=True, exist_ok=True)
    (checkout / "dotclaude" / "CLAUDE.md").write_bytes(SEED_F)
    (checkout / "dotclaude" / "skills" / "S.md").write_bytes(SEED_S)
    _write_manifest(checkout)


def _seed_live(live: Path, content_f: bytes = SEED_F, content_s: bytes = SEED_S,
               crlf: bool = False) -> None:
    (live / "skills").mkdir(parents=True, exist_ok=True)
    for rel, content in ((F_TARGET, content_f), (S_TARGET, content_s)):
        blob = content.replace(b"\n", b"\r\n") if crlf else content
        (live / rel).write_bytes(blob)


def new_world(variants: dict[str, types.ModuleType], *,
             desktop_live_seeded: bool = True,
             desktop_live_crlf: bool = False,
             desktop_pre_existing: tuple[bytes, bytes] | None = None) -> World:
    """Build R, C1(laptop checkout, seeded+pushed), L1(laptop live, in sync),
    C2(desktop checkout, cloned from R), L2(desktop live).

    desktop_pre_existing: if given, (content_f, content_s) L2 starts with,
    BEFORE C2 is cloned -- scenario G, the adoption case: desktop already
    has its own unrelated config, then clones the shared payload fresh.
    desktop_live_seeded=False leaves L2 entirely absent (no CLAUDE.md/skills
    at all yet).
    """
    tmp = Path(tempfile.mkdtemp(prefix="ccs-sim-world-"))
    bare = tmp / "R.git"
    _init_repo(bare, bare=True)

    laptop_co = tmp / "laptop" / "checkout"
    laptop_live = tmp / "laptop" / "live"
    laptop_uc = tmp / "laptop" / "userclaude"
    laptop_uc.mkdir(parents=True, exist_ok=True)
    _git(tmp, "clone", "-q", str(bare), str(laptop_co))
    _seed_checkout(laptop_co)
    _git(laptop_co, "add", "-A")
    _git(laptop_co, "commit", "-q", "-m", "seed",
        env={"GIT_AUTHOR_DATE": str(_tick()), "GIT_COMMITTER_DATE": str(_CLOCK["t"])})
    _git(laptop_co, "push", "-q", "-u", "origin", "main")
    _seed_live(laptop_live)  # L1 starts already-applied (in sync)

    desktop_co = tmp / "desktop" / "checkout"
    desktop_live = tmp / "desktop" / "live"
    desktop_uc = tmp / "desktop" / "userclaude"
    desktop_uc.mkdir(parents=True, exist_ok=True)
    if desktop_pre_existing is not None:
        cf, cs = desktop_pre_existing
        _seed_live(desktop_live, content_f=cf, content_s=cs, crlf=desktop_live_crlf)
    elif desktop_live_seeded:
        _seed_live(desktop_live, crlf=desktop_live_crlf)
    else:
        desktop_live.mkdir(parents=True, exist_ok=True)
    _git(tmp, "clone", "-q", str(bare), str(desktop_co))

    laptop = Machine("laptop", laptop_live, laptop_co, laptop_uc)
    desktop = Machine("desktop", desktop_live, desktop_co, desktop_uc)
    world = World(tmp, bare, laptop, desktop, variants)
    # Establish the initial sync point P for both machines' already-applied
    # state (matches by construction unless desktop_pre_existing was given,
    # in which case desktop correctly gets NO sync point -- SC-80 adoption).
    _record_sync_points(world, "laptop")
    _record_sync_points(world, "desktop")
    return world


def add_machine(world: World, name: str, *, seeded: bool = True) -> Machine:
    """Clone a THIRD (or Nth) machine from the same bare repo R -- scenario O
    (SC-74, three machines). Registered in world.extra_machines so
    world.machine(name) resolves it."""
    co = world.tmpdir / name / "checkout"
    live = world.tmpdir / name / "live"
    uc = world.tmpdir / name / "userclaude"
    uc.mkdir(parents=True, exist_ok=True)
    if seeded:
        _seed_live(live)
    else:
        live.mkdir(parents=True, exist_ok=True)
    _git(world.tmpdir, "clone", "-q", str(world.bare), str(co))
    m = Machine(name, live, co, uc)
    world.extra_machines[name] = m
    _record_sync_points(world, name)
    return m


# --------------------------------------------------------------------------
# Primitives (operate through the PRISTINE variant's collect/apply -- those
# modules are byte-identical between variants; only merge.py differs)
# --------------------------------------------------------------------------

def _roots(m: Machine) -> dict[str, Path]:
    return {"CLAUDE_DIR": m.live, "USER_CLAUDE": m.user_claude}


def _tracked_paths(extra: tuple[str, str] | None = None) -> tuple[tuple[str, str], ...]:
    base = ((F_REPO, F_TARGET), (S_REPO, S_TARGET))
    return base + (extra,) if extra else base


def _record_sync_points(world: World, which: str, extra: tuple[str, str] | None = None) -> None:
    """Update world.sync_point for every tracked path where live now
    matches the checkout's current HEAD -- the DWP's two P-advancing
    moments (apply: P := H "at that moment"; collect+commit: P := the new
    commit), both of which reduce to "live equals current HEAD content" at
    the instant this is called. Never guesses; only records what actually
    just happened in THIS harness-controlled world.
    """
    m = world.machine(which)
    head_sha = _git_text(m.checkout, "rev-parse", "HEAD", check=False).strip()
    if not head_sha:
        return
    for repo_path, target_rel in _tracked_paths(extra):
        live_path = m.live / target_rel
        if not live_path.is_file():
            continue
        head_bytes = git_show(m.checkout, repo_path, "HEAD")
        if head_bytes is None:
            continue
        if _normalize_eol(live_path.read_bytes()) == _normalize_eol(head_bytes):
            world.sync_point[(which, repo_path)] = head_sha


def edit_live(m: Machine, target_rel: str, content: bytes, crlf: bool = False) -> None:
    p = m.live / target_rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content.replace(b"\n", b"\r\n") if crlf else content)


def delete_live(m: Machine, target_rel: str) -> None:
    p = m.live / target_rel
    if p.is_file():
        p.unlink()


def collect(world: World, which: str, dry_run: bool = False):
    mech = world.variants["pristine"]
    m = world.machine(which)
    manifest = mech.manifest.Manifest.load(m.checkout)
    return mech.collect.collect(manifest, m.checkout, _roots(m), repo=None, dry_run=dry_run)


def apply(world: World, which: str, dry_run: bool = False, sync_removals: bool = False):
    mech = world.variants["pristine"]
    m = world.machine(which)
    manifest = mech.manifest.Manifest.load(m.checkout)
    backups = m.user_claude / "backups" / "ccs"
    result = mech.apply.apply(manifest, m.checkout, _roots(m), backups, repo=None,
                              dry_run=dry_run, sync_removals=sync_removals)
    if not dry_run:
        _record_sync_points(world, which)  # P := H "at that moment" (DWP Stage 0.5)
    return result


def write_checkout(world: World, which: str, repo_rel: str, content: bytes) -> None:
    """Write straight into the checkout's working tree, bypassing collect().

    collect() sweeps the WHOLE live tree (every modified/live-only file in
    the manifest), not just one path -- so it is the wrong primitive for
    "the checkout gained unrelated local activity" (a real, separate git
    commit made directly in the checkout, the way a stray edit or a git-
    level merge would). Using collect() here would also silently drag
    live's stale F/S content back over the checkout's freshly-pulled copy,
    which is a real ccs behaviour but not the one this scenario tests.
    """
    m = world.machine(which)
    p = m.checkout / repo_rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


def commit(world: World, which: str, msg: str) -> str:
    m = world.machine(which)
    _git(m.checkout, "add", "-A")
    t = _tick()
    p = _git(m.checkout, "commit", "-q", "-m", msg,
             env={"GIT_AUTHOR_DATE": str(t), "GIT_COMMITTER_DATE": str(t)},
             check=False)
    if p.returncode != 0:
        world.note(f"{which}: commit {msg!r} -- nothing to commit")
        return ""
    sha = _git_text(m.checkout, "rev-parse", "HEAD").strip()
    # P := the new commit, for every tracked path this commit brought into
    # agreement with live (DWP Stage 0.5's "collect+commit" P-advance).
    _record_sync_points(world, which)
    return sha


def push(world: World, which: str, check: bool = True):
    m = world.machine(which)
    return _git(m.checkout, "push", "-q", "origin", "main", check=check)


def pull(world: World, which: str, check: bool = True):
    m = world.machine(which)
    t = _tick()
    return _git(m.checkout, "pull", "-q", "--no-rebase", "origin", "main",
               env={"GIT_AUTHOR_DATE": str(t), "GIT_COMMITTER_DATE": str(t)},
               check=check)


# --------------------------------------------------------------------------
# Ground truth (never touches ccs)
# --------------------------------------------------------------------------

def truth(world: World, which: str, repo_path: str, target_rel: str) -> dict:
    """Ground truth, independent of ccs.

    IMPORTANT #1: `git log --follow -- path` (the same call infer_base
    makes) can silently ELIDE a merge commit from a path's history entirely
    -- verified empirically in scenario J, where HEAD was a two-parent merge
    commit and `--follow`'s list started with one of its PARENTS, not the
    merge commit itself. So `shas[0]` is NOT a safe proxy for "HEAD's
    current content" in general. HEAD's content is always fetched directly
    via `git show HEAD:path`, independent of the --follow-based candidate
    list; the candidate list is only used to search for a matching ancestor
    on the CHECKOUT-AHEAD side.

    IMPORTANT #2 (batch 2 / SC-14, the revert trap): "H equals some older
    commit hk" is NOT, by itself, sufficient to call a path one-sided
    live-ahead -- the exhaustive-scenario-space DWP's Stage 0.5 is explicit
    that truth is a function of P (the actual last sync point), and a
    checkout REVERT can make H equal an old commit that is NOT P while the
    checkout genuinely moved away from P in between (P -> edit -> revert
    back through an ancestor's content). Content-equality alone cannot tell
    "H is unchanged since P" from "H happens to look like something old."
    So the live-ahead check here uses world.sync_point -- P as WATCHED by
    this harness (apply, or collect+commit), never inferred from content --
    and asks specifically "does H's current content equal P's content?".
    The checkout-ahead direction does NOT need this: DWP Stage 3 (SC-10)
    is explicit that "L equals some historical commit" is safe/correct as
    one-sided REGARDLESS of whether that commit is literally P, because L
    still holds nothing unique either way. Only the mirror direction has a
    hazard, which is exactly what SC-14 vs SC-11/12/13 turns on.
    """
    m = world.machine(which)
    shas = git_log_shas(m.checkout, repo_path)
    live_path = m.live / target_rel
    if not shas:
        return {"verdict": "untracked", "base_sha": None}
    if not live_path.is_file():
        return {"verdict": "live-missing", "base_sha": None}
    head_sha_full = _git_text(m.checkout, "rev-parse", "HEAD").strip()
    head_bytes = git_show(m.checkout, repo_path, "HEAD")
    if head_bytes is None:
        # Live is present (checked above), the path HAS history (shas is
        # non-empty), but HEAD lacks it now -- a commit deleted it from the
        # checkout (SC-33), distinct from content divergence.
        return {"verdict": "checkout-deleted", "base_sha": None}
    head_n = _normalize_eol(head_bytes)
    live_n = _normalize_eol(live_path.read_bytes())
    if live_n == head_n:
        return {"verdict": "in-sync", "base_sha": head_sha_full[:7]}
    # Checkout-ahead: does live match ANY historical commit? Safe/correct
    # regardless of whether that commit is literally P (SC-10/SC-15).
    for sha in shas:
        blob = git_show(m.checkout, repo_path, sha)
        if blob is not None and _normalize_eol(blob) == live_n:
            return {"verdict": "one-sided-checkout-ahead", "base_sha": sha[:7]}
    # Live-ahead: STRICTLY P-based (SC-11/12/13 vs SC-14). Fetches P's
    # content directly by sha -- never via the --follow list, which may not
    # even contain P (e.g. a merge commit, per IMPORTANT #1).
    p_sha = world.sync_point.get((which, repo_path))
    if p_sha is not None:
        p_blob = git_show(m.checkout, repo_path, p_sha)
        if p_blob is not None and _normalize_eol(p_blob) == head_n:
            return {"verdict": "one-sided-live-ahead", "base_sha": p_sha[:7]}
    return {"verdict": "two-sided", "base_sha": None}


# --------------------------------------------------------------------------
# ccs's own claims, under one variant
# --------------------------------------------------------------------------

def _status_bucket(diffs, entry_repo: str, rel: str) -> str:
    for d in diffs:
        if d.entry.repo == entry_repo:
            if rel in d.modified:
                return "modified"
            if rel in d.live_only:
                return "live_only"
            if rel in d.repo_only:
                return "repo_only"
            return "clean"
    return "no-entry"


# (kind, repo_path, target_rel, rel-within-entry, entry.repo, guard_target)
# guard_target is the string two_way_labels would emit (== entry.target);
# for anything under the "skills" directory entry that is entry.target
# itself ("skills"), which TW1 never even reaches (is_file() on a dir is
# False), regardless of which file inside changed.
STANDARD_SPECS = (
    ("file", F_REPO, F_TARGET, "", F_REPO, "CLAUDE.md"),
    ("dir", S_REPO, S_TARGET, "S.md", "dotclaude/skills", "skills"),
)


def _diagnose_one(mod, m: Machine, all_diffs, two_way,
                  spec: tuple) -> dict:
    kind, repo_path, target_rel, rel_in_entry, entry_repo, guard_target = spec
    live_path = m.live / target_rel
    bucket = _status_bucket(all_diffs, entry_repo, rel_in_entry)
    guard_flagged = guard_target in two_way

    infer = None
    if live_path.is_file():
        head_bytes = git_show(m.checkout, repo_path, "HEAD")
        if head_bytes is not None:
            ours_b = live_path.read_bytes()
            if _normalize_eol(ours_b) != _normalize_eol(head_bytes):
                found = mod.merge.infer_base(m.checkout, repo_path, ours_b, head_bytes)
                if found is not None:
                    _, sha = found
                    infer = {"sha": sha,
                             "is_ancestor": git_is_ancestor(m.checkout, sha, "HEAD")}
                else:
                    infer = None
            else:
                infer = "in-sync"
    return {"repo_path": repo_path, "target": target_rel,
           "status_bucket": bucket, "guard_flagged_both": guard_flagged,
           "infer_base": infer}


def diagnose(world: World, variant_name: str, which: str, specs=STANDARD_SPECS) -> dict:
    """Everything ccs's machinery says about each spec'd path on one machine,
    under one variant: status bucket (diff_all -- worktree compare, what
    `ccs status` text is built from), guard verdict (two_way_labels --
    HEAD compare, what actually blocks collect/apply), and infer_base's
    raw pick (called directly -- see report note on _head_items/TW1 both
    being is_file()-gated, so this can show what infer_base WOULD find
    even where the real pipeline never asks it).
    """
    mod = world.variants[variant_name]
    m = world.machine(which)
    manifest = mod.manifest.Manifest.load(m.checkout)
    roots = _roots(m)
    all_diffs = mod.syncmap.diff_all(manifest, m.checkout, roots)
    two_way = mod.merge.two_way_labels(manifest, m.checkout, roots)
    return {spec[0]: _diagnose_one(mod, m, all_diffs, two_way, spec) for spec in specs}


def snapshot(world: World, which: str, note: str, extra: tuple | None = None) -> dict:
    """The unit of report data: truth + both variants' claims, for the
    standard two entries (and optionally one extra ad hoc path -- e.g. a
    brand-new untracked file for scenario H) on one machine, at one point
    in the timeline.

    extra, if given: (kind_label, repo_path, target_rel) for a path OUTSIDE
    the standard F/S pair, still living under the "skills" directory entry
    so its guard_target is "skills" (and therefore always TW1-invisible).
    """
    specs = list(STANDARD_SPECS)
    if extra is not None:
        kind_label, repo_path, target_rel = extra
        specs.append((kind_label, repo_path, target_rel, Path(target_rel).name,
                     "dotclaude/skills", "skills"))
    ev = {"note": note, "machine": which,
         "truth": {spec[0]: truth(world, which, spec[1], spec[2]) for spec in specs}}
    for vname in world.variants:
        ev[vname] = diagnose(world, vname, which, specs=tuple(specs))
    return ev


def verdict(ev: dict, kind: str, variant_name: str) -> str:
    """agree / false-both / false-one-sided / silent-overwrite-risk, for
    one entry kind under one variant, at one snapshot."""
    t = ev["truth"][kind]["verdict"]
    g = ev[variant_name][kind]
    flagged = g["guard_flagged_both"]
    two_sided_truth = t == "two-sided"
    if t == "untracked":
        return "n/a (untracked -- not a merge candidate; local snapshot in checkout, if any, is stale)"
    if t == "live-missing":
        return "n/a (live absent -- SC-31/SC-30 territory, not a two_way_labels question)"
    if t == "checkout-deleted":
        return "n/a (checkout deleted it -- SC-33, not a two_way_labels question)"
    if t == "in-sync":
        return "n/a"
    if flagged and two_sided_truth:
        return "agree (correctly refused)"
    if flagged and not two_sided_truth:
        return "false-both (spurious refusal)"
    if not flagged and two_sided_truth:
        return "silent-overwrite-risk (should have refused, did not)"
    return "agree (correctly allowed)"


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------

SCENARIOS: dict[str, "callable"] = {}


def scenario(letter):
    def deco(fn):
        SCENARIOS[letter] = fn
        return fn
    return deco


@scenario("A")
def scenario_A(world):
    """Clean one-way: laptop edits F and S; collect; commit; push; desktop
    pulls; status BEFORE apply; apply; status after."""
    events = []
    edit_live(world.laptop, F_TARGET, SEED_F + b"laptop-line4\n")
    edit_live(world.laptop, S_TARGET, SEED_S + b"skillD\n")
    collect(world, "laptop")
    commit(world, "laptop", "A: laptop edits F and S")
    push(world, "laptop")
    pull(world, "desktop")
    events.append(snapshot(world, "desktop", "A: after pull, before apply"))
    apply(world, "desktop")
    events.append(snapshot(world, "desktop", "A: after apply"))
    return events


@scenario("B")
def scenario_B(world):
    """Tonight's real case: desktop pulled and did NOT apply, then edited F
    and S locally on DIFFERENT lines than laptop -- both sides genuinely
    changed."""
    events = []
    edit_live(world.laptop, F_TARGET, SEED_F + b"laptop-line4\n")
    edit_live(world.laptop, S_TARGET, SEED_S + b"skillD\n")
    collect(world, "laptop")
    commit(world, "laptop", "B: laptop edits F and S")
    push(world, "laptop")
    pull(world, "desktop")  # desktop's checkout now has laptop's commit; live untouched
    # desktop edits the SAME files, different lines than laptop touched
    edit_live(world.desktop, F_TARGET, b"desktop-line0\n" + SEED_F)
    edit_live(world.desktop, S_TARGET, b"skillZero\n" + SEED_S)
    events.append(snapshot(world, "desktop", "B: genuinely two-sided, before status guard"))
    return events


@scenario("Bp")
def scenario_Bp(world):
    """B': desktop pulled, then edited only OTHER files (a new skill) --
    F and S must read one-sided despite local checkout activity."""
    events = []
    edit_live(world.laptop, F_TARGET, SEED_F + b"laptop-line4\n")
    edit_live(world.laptop, S_TARGET, SEED_S + b"skillD\n")
    collect(world, "laptop")
    commit(world, "laptop", "Bp: laptop edits F and S")
    push(world, "laptop")
    pull(world, "desktop")
    # desktop gains UNRELATED checkout activity -- written directly into the
    # checkout (not through collect(), which would also sweep up F/S; see
    # write_checkout's docstring). live's F/S are never touched.
    write_checkout(world, "desktop", "dotclaude/skills/Other.md", b"unrelated skill\n")
    commit(world, "desktop", "Bp: desktop adds an unrelated skill file")
    events.append(snapshot(world, "desktop",
                           "Bp: local activity on OTHER files only -- F/S still one-sided"))
    return events


@scenario("C")
def scenario_C(world):
    """Live equals an older (middle) commit: three commits on F; desktop's
    live == the middle commit's content."""
    events = []
    c1 = SEED_F + b"c1-marker\n"  # distinct from the seed commit's content --
    # otherwise this commit is a content no-op (nothing to commit) and history
    # collapses to 2 new commits instead of 3.
    c2 = c1 + b"middle-addition\n"
    c3 = c2 + b"final-addition\n"
    for content, msg in ((c1, "C: c1"), (c2, "C: c2 middle"), (c3, "C: c3 final")):
        edit_live(world.laptop, F_TARGET, content)
        collect(world, "laptop")
        commit(world, "laptop", msg)
    push(world, "laptop")
    pull(world, "desktop")
    edit_live(world.desktop, F_TARGET, c2)  # desktop live == the MIDDLE commit
    events.append(snapshot(world, "desktop", "C: live == middle commit (c2), HEAD == c3"))
    return events


@scenario("D")
def scenario_D(world):
    """Mirror with deletions (Addendum 2): checkout content unchanged
    net (a revert makes an older commit equal HEAD); live deleted >= 3
    lines. D' variant: only 1-2 deletions, below PHANTOM_MIN_LINES=3.

    Desktop's P is established with a REAL apply() at c1 (six lines) before
    the add-then-revert sequence, not left to coincide with an old commit's
    content by chance -- required for truth()'s P-based live-ahead check
    (batch 2 / SC-14) to honestly certify this as one-sided rather than
    accidentally landing there the way content-matching alone would."""
    events = []
    six = b"l1\nl2\nl3\nl4\nl5\nl6\n"
    three = b"l1\nl2\nl3\n"
    five = b"l1\nl2\nl3\nl4\nl5\n"
    edit_live(world.laptop, F_TARGET, six)
    collect(world, "laptop"); commit(world, "laptop", "D: c1 six lines"); push(world, "laptop")
    pull(world, "desktop"); apply(world, "desktop")  # P := c1 (six), for real
    edit_live(world.laptop, F_TARGET, six + b"X\n")
    collect(world, "laptop"); commit(world, "laptop", "D: c2 add X"); push(world, "laptop")
    pull(world, "desktop")
    edit_live(world.laptop, F_TARGET, six)
    collect(world, "laptop"); commit(world, "laptop", "D: c3 revert to six"); push(world, "laptop")
    pull(world, "desktop")  # HEAD == c3 (content six, byte-identical to P's c1 but a different commit)
    edit_live(world.desktop, F_TARGET, three)  # deleted l4-l6 (>= PHANTOM_MIN_LINES)
    events.append(snapshot(world, "desktop", "D: live deleted l4-l6 (3 lines), checkout reverted"))

    edit_live(world.desktop, F_TARGET, five)  # D': deleted only l6 (below threshold)
    events.append(snapshot(world, "desktop", "D': live deleted only l6 (1 line, below threshold)"))
    return events


@scenario("E")
def scenario_E(world):
    """Genuine two-sided WITH deletions: live deletes >= 3 lines AND the
    checkout independently changed those same lines (not a revert) --
    does the phantom check reject the one candidate, and is 'both' right
    for the right reason (truly two-sided) here?"""
    events = []
    six = b"l1\nl2\nl3\nl4\nl5\nl6\n"
    replaced = b"l1\nl2\nl3\nY1\nY2\nY3\n"  # theirs replaces l4-6 with new content
    edit_live(world.laptop, F_TARGET, six)
    collect(world, "laptop")
    commit(world, "laptop", "E: c1 six lines")
    edit_live(world.laptop, F_TARGET, replaced)
    collect(world, "laptop")
    commit(world, "laptop", "E: c2 theirs replaces l4-6 with Y1-Y3")
    push(world, "laptop")
    pull(world, "desktop")
    edit_live(world.desktop, F_TARGET, three := b"l1\nl2\nl3\n")  # live deletes l4-6 outright
    events.append(snapshot(world, "desktop",
                           "E: live deleted l4-6, checkout independently replaced them"))
    return events


@scenario("F")
def scenario_F(world):
    """Ping-pong, three rounds: laptop edits->push; desktop pulls+applies,
    edits->push; laptop pulls (does NOT apply), edits the same file ->
    status. Tracks which commit each side's live equals at each step."""
    events = []
    r1 = SEED_F + b"round1-laptop\n"
    edit_live(world.laptop, F_TARGET, r1)
    collect(world, "laptop")
    commit(world, "laptop", "F: round1 laptop")
    push(world, "laptop")

    pull(world, "desktop")
    apply(world, "desktop")
    events.append(snapshot(world, "desktop", "F: round1, desktop pulled+applied"))
    r2 = r1 + b"round2-desktop\n"
    edit_live(world.desktop, F_TARGET, r2)
    collect(world, "desktop")
    commit(world, "desktop", "F: round2 desktop")
    push(world, "desktop")

    pull(world, "laptop")  # laptop does NOT apply -- live still == r1
    events.append(snapshot(world, "laptop", "F: round2, laptop pulled but did not apply"))
    r3 = r1 + b"round3-laptop-own-branch\n"
    edit_live(world.laptop, F_TARGET, r3)  # laptop edits the SAME file independently
    events.append(snapshot(world, "laptop", "F: round3, laptop edited live without applying r2"))
    return events


@scenario("G")
def scenario_G(world):
    """Adoption / no shared history: desktop already has its OWN non-empty
    live config, then clones R fresh. Is 'both'/refuse the right call?"""
    # rebuilt with pre-existing desktop content instead of the default world
    w2 = new_world(world.variants,
                  desktop_pre_existing=(b"desktop-own-memory.md content\n",
                                        b"desktop-own-skill.md content\n"))
    events = [snapshot(w2, "desktop", "G: desktop had its own config, then cloned R fresh")]
    return events


@scenario("H")
def scenario_H(world):
    """Stale uncommitted collect (the test-mutation case): laptop collects
    a brand-new file into C1 but never commits, keeps editing L1 further.
    The checkout working tree holds an untracked snapshot older than live."""
    events = []
    extra = ("new", "dotclaude/skills/NewTool.md", "skills/NewTool.md")
    edit_live(world.laptop, "skills/NewTool.md", b"new tool v1\n")
    collect(world, "laptop")  # writes into C1 working tree, NOT committed
    events.append(snapshot(world, "laptop", "H: collected but uncommitted (v1)", extra=extra))
    edit_live(world.laptop, "skills/NewTool.md", b"new tool v1\nv2 addition\n")
    events.append(snapshot(world, "laptop",
                           "H: live kept moving (v2) -- checkout has stale uncommitted v1",
                           extra=extra))
    return events


@scenario("I")
def scenario_I(world):
    """CRLF: same edit as A, but written to live with CRLF while the
    checkout stores LF. No phantom two-sided from line endings alone."""
    events = []
    edit_live(world.laptop, F_TARGET, SEED_F + b"laptop-line4\n")
    edit_live(world.laptop, S_TARGET, SEED_S + b"skillD\n")
    collect(world, "laptop")
    commit(world, "laptop", "I: laptop edits F and S")
    push(world, "laptop")
    pull(world, "desktop")
    apply(world, "desktop")
    # Re-write desktop's live copies with CRLF, SAME logical content --
    # must read as clean/in-sync, not as drift.
    edit_live(world.desktop, F_TARGET, SEED_F + b"laptop-line4\n", crlf=True)
    edit_live(world.desktop, S_TARGET, SEED_S + b"skillD\n", crlf=True)
    events.append(snapshot(world, "desktop", "I: live is CRLF, checkout is LF, same content"))
    return events


@scenario("J")
def scenario_J(world):
    """Unpushed commit on the other side: laptop collects+commits but does
    NOT push; desktop edits the same file (different region) and pushes;
    laptop pulls (a real git merge commit results) -> status on laptop."""
    events = []
    laptop_v1 = SEED_F + b"laptop-tail\n"
    edit_live(world.laptop, F_TARGET, laptop_v1)
    collect(world, "laptop")
    commit(world, "laptop", "J: laptop appends tail (NOT pushed yet)")
    # NOTE: no push here -- laptop's commit stays local.

    desktop_v1 = b"desktop-head\n" + SEED_F
    edit_live(world.desktop, F_TARGET, desktop_v1)
    collect(world, "desktop")
    commit(world, "desktop", "J: desktop inserts head line")
    push(world, "desktop")

    pull(world, "laptop")  # non-conflicting: git auto-merges into a merge commit
    events.append(snapshot(world, "laptop",
                           "J: laptop pulled desktop's push over its own unpushed commit "
                           "(git created a merge commit); live untouched since laptop's own commit"))
    # laptop now edits the SAME file again, on top of the merge
    edit_live(world.laptop, F_TARGET, laptop_v1 + b"laptop-round2\n")
    events.append(snapshot(world, "laptop", "J: laptop edits again after the merge commit"))
    return events


# --------------------------------------------------------------------------
# Batch 2 scenarios (exhaustive-scenario-space DWP, Stage 3/4a)
# --------------------------------------------------------------------------

@scenario("K")
def scenario_K(world):
    """K / SC-14, the revert trap -- THE scenario distinguishing naive S1
    (V2) from the refined rule (V3). Desktop syncs at P (content Z, via a
    REAL apply()). Laptop pushes a REVERT: a new commit whose content
    equals an OLDER commit X (X == the world's own seed content, which
    precedes Z in F's history). Desktop pulls (does NOT apply) -- HEAD == X
    by content, but X is NOT desktop's P. Desktop live = Z + an added line.
    Truth: two-sided (live changed Z->L, checkout changed Z->X).
    Prediction: V2 short-circuits on cand==theirs_n(X) and returns it
    immediately -> one-sided live-ahead -> `collect` would silently
    overwrite the revert (LOSS). V3 lets X compete on score against Z and,
    for this modest live edit, correctly prefers Z -> two-sided."""
    events = []
    X = SEED_F
    Z = SEED_F + b"Z-addition\n"
    edit_live(world.laptop, F_TARGET, Z)
    collect(world, "laptop"); commit(world, "laptop", "K: laptop edits to Z (this becomes P)")
    push(world, "laptop")
    pull(world, "desktop"); apply(world, "desktop")  # desktop syncs AT P == Z, for real
    events.append(snapshot(world, "desktop", "K: desktop synced at P=Z (control)"))
    edit_live(world.laptop, F_TARGET, X)  # laptop reverts to X's exact bytes
    collect(world, "laptop"); commit(world, "laptop", "K: laptop reverts to X")
    push(world, "laptop")
    pull(world, "desktop")  # does NOT apply -- HEAD == X by content, live still Z
    edit_live(world.desktop, F_TARGET, Z + b"desktop-line\n")  # live = Z + edit = L
    events.append(snapshot(world, "desktop", "K / SC-14: the revert trap"))
    return events


@scenario("Kp")
def scenario_Kp(world):
    """K' -- same trap, but live's edit is LARGE (a near-total rewrite) so
    that X (the revert target, WRONG) happens to score nearer to live than
    Z (the true P, RIGHT) does under plain edit-distance. Prediction: V3
    also picks X here -- documents the residual limit of any history walk
    (recording P for real, the DWP's deferred S3, is what actually closes
    this; distance-based scoring is a heuristic, not a proof)."""
    events = []
    x_lines = [f"x{i}".encode() for i in range(1, 11)]
    X = b"\n".join(x_lines) + b"\n"
    z_extra = [f"z{i}".encode() for i in range(1, 6)]
    Z = X + b"\n".join(z_extra) + b"\n"
    new_lines = [f"new{i}".encode() for i in range(1, 11)]
    L = b"\n".join(x_lines[:5]) + b"\n" + b"\n".join(new_lines) + b"\n"

    edit_live(world.laptop, F_TARGET, X)
    collect(world, "laptop"); commit(world, "laptop", "Kp: laptop establishes X"); push(world, "laptop")
    edit_live(world.laptop, F_TARGET, Z)
    collect(world, "laptop"); commit(world, "laptop", "Kp: laptop edits to Z (P)"); push(world, "laptop")
    pull(world, "desktop"); apply(world, "desktop")  # desktop syncs at P == Z, for real
    edit_live(world.laptop, F_TARGET, X)  # laptop reverts to X
    collect(world, "laptop"); commit(world, "laptop", "Kp: laptop reverts to X"); push(world, "laptop")
    pull(world, "desktop")  # does NOT re-apply
    edit_live(world.desktop, F_TARGET, L)  # large rewrite, coincidentally nearer to X than to Z
    events.append(snapshot(world, "desktop",
                           "K' / SC-14 residual limit: live rewrite scores nearer to X than to P=Z"))
    return events


@scenario("L")
def scenario_L(world):
    """L / SC-03: live == HEAD (in sync by that measure), but the
    checkout's WORKING TREE has an uncommitted edit -- someone edited the
    file directly inside the checkout folder, or a `ccs collect` was
    interrupted before a commit. Truth (L-vs-H) says in-sync; the hazard is
    entirely in W (the working tree), which truth() does not even model --
    status_bucket (diff_all, an L-vs-W comparison) is the only thing that
    sees it, and the guard (two_way_labels, an L-vs-H comparison) is
    completely blind to it: `ours == theirs` short-circuits before W is
    ever consulted. Probe: `collect --dry-run` -- does it propose
    overwriting the checkout's uncommitted edit with live's (unrelated,
    HEAD-matching) content?"""
    events = []
    write_checkout(world, "laptop", F_REPO, SEED_F + b"uncommitted-checkout-edit\n")
    ev = snapshot(world, "laptop", "L / SC-03: live==HEAD, but checkout working tree has an uncommitted edit")
    dry = collect(world, "laptop", dry_run=True)
    ev["collect_dry_run"] = {"copied": list(dry.copied),
                             "would_overwrite_uncommitted_edit": F_REPO in
                             [c if isinstance(c, str) else c for c in dry.copied]}
    events.append(ev)
    return events


@scenario("M")
def scenario_M(world):
    """M / SC-31 and SC-33: deletion propagation, both directions, via two
    dedicated ad hoc files inside the "skills" directory entry.

    SC-31: present in history and in HEAD, ABSENT in live (desktop deleted
    it locally). `apply --dry-run` restores it -- silently undoing what may
    have been an intentional deletion; nothing asks first.

    SC-33: present in live, DELETED from HEAD by a real commit (removed
    from the payload and pushed). `apply` (default, no --sync-removals)
    reports removals_pending rather than deleting live's copy -- the SAFE
    default; --sync-removals is the explicit opt-in that actually stages
    the removal."""
    events = []

    # -- SC-31: live-side deletion, checkout+history still have it --------
    m31_repo, m31_target = "dotclaude/skills/M31.md", "skills/M31.md"
    edit_live(world.laptop, m31_target, b"m31 v1\n")
    collect(world, "laptop"); commit(world, "laptop", "M: add M31.md"); push(world, "laptop")
    pull(world, "desktop"); apply(world, "desktop")  # desktop has it too, in sync
    delete_live(world.desktop, m31_target)  # desktop deletes it locally, never told ccs
    ev31 = snapshot(world, "desktop", "M / SC-31: live deleted M31.md; checkout+history still have it",
                    extra=("m31", m31_repo, m31_target))
    dry31 = apply(world, "desktop", dry_run=True)
    ev31["apply_dry_run"] = {"copied": list(dry31.copied),
                             "would_restore_the_deleted_file": m31_target in dry31.copied}
    events.append(ev31)

    # -- SC-33: checkout-side deletion via a real commit -------------------
    m33_repo, m33_target = "dotclaude/skills/M33.md", "skills/M33.md"
    edit_live(world.laptop, m33_target, b"m33 v1\n")
    collect(world, "laptop"); commit(world, "laptop", "M: add M33.md"); push(world, "laptop")
    pull(world, "desktop"); apply(world, "desktop")  # both machines have it, in sync
    (world.laptop.checkout / m33_repo).unlink()
    commit(world, "laptop", "M: remove M33.md from the payload"); push(world, "laptop")
    pull(world, "desktop")  # desktop's checkout loses it; desktop's LIVE still has it, untouched
    ev33 = snapshot(world, "desktop", "M / SC-33: a checkout commit deleted M33.md; live still has it",
                    extra=("m33", m33_repo, m33_target))
    dry_default = apply(world, "desktop", dry_run=True)
    dry_sync = apply(world, "desktop", dry_run=True, sync_removals=True)
    ev33["apply_dry_run_default"] = {"removals_pending": list(dry_default.removals_pending),
                                     "local_only": list(dry_default.local_only)}
    ev33["apply_dry_run_sync_removals"] = {"removals_staged": list(dry_sync.removals_staged)}
    events.append(ev33)
    return events


@scenario("N")
def scenario_N(world):
    """N / SC-72: the 25-commit window. P is established for real (a real
    apply()); 26 MORE commits touch F afterward without desktop ever
    re-applying, so live stays at P's content the whole time -- P becomes
    the 26th-oldest candidate once HEAD is excluded, one past
    infer_base's default max_commits=25. Prediction: P falls outside the
    window for ALL THREE variants -> None -> both/refuse, regardless of
    the S1/V3 equality rule -- a window miss is a SEPARATE failure from
    the equality guard. Also reports each variant's answer with
    max_commits explicitly raised to 30 (infer_base's cap is a per-call
    parameter, not a module constant, so this calls it directly rather
    than monkeypatching module state)."""
    events = []
    p_content = SEED_F + b"P-content\n"
    edit_live(world.laptop, F_TARGET, p_content)
    collect(world, "laptop"); commit(world, "laptop", "N: c1 (P)"); push(world, "laptop")
    pull(world, "desktop"); apply(world, "desktop")  # P established, for real
    content = p_content
    for i in range(2, 28):  # c2 .. c27 -- 26 more commits; HEAD ends at c27
        content = content + f"line-{i}\n".encode()
        edit_live(world.laptop, F_TARGET, content)
        collect(world, "laptop"); commit(world, "laptop", f"N: c{i}"); push(world, "laptop")
    pull(world, "desktop")  # desktop's checkout catches up; live stays at P's content
    ev = snapshot(world, "desktop", "N / SC-72: 26 commits since P; live==P; default max_commits=25")

    m = world.desktop
    head_bytes = git_show(m.checkout, F_REPO, "HEAD")
    ours_b = (m.live / F_TARGET).read_bytes()
    raised = {}
    for vname, mod in world.variants.items():
        found = mod.merge.infer_base(m.checkout, F_REPO, ours_b, head_bytes, max_commits=30)
        raised[vname] = ({"sha": found[1], "is_ancestor": git_is_ancestor(m.checkout, found[1], "HEAD")}
                         if found else None)
    ev["max_commits_30"] = raised
    events.append(ev)
    return events


@scenario("O")
def scenario_O(world):
    """O / SC-74: three machines. M3 (a THIRD checkout+live, cloned fresh
    from the same bare repo R) pushes an edit to F that NEITHER M1
    (laptop) nor M2 (desktop) authored. Both M1 and M2 pull without
    applying -- both should read as SC-10 (one-sided checkout-ahead,
    live==P), and infer_base's offered base should be P, a commit neither
    of them wrote."""
    events = []
    m3 = add_machine(world, "m3")
    edit_live(m3, F_TARGET, SEED_F + b"m3-edit\n")
    collect(world, "m3"); commit(world, "m3", "O: M3 edits F"); push(world, "m3")
    pull(world, "laptop")
    pull(world, "desktop")
    events.append(snapshot(world, "laptop", "O / SC-74: M1 pulled M3's edit, did not apply"))
    events.append(snapshot(world, "desktop", "O / SC-74: M2 pulled M3's edit, did not apply"))
    return events


@scenario("P")
def scenario_P(world):
    """P / SC-42, single probe: force-push. Desktop syncs at P (content
    Z, via a REAL apply()). Laptop rewrites history (`commit --amend`
    replaces P's commit with DIFFERENT content Z', a new sha) and
    force-pushes -- P is no longer reachable from the new HEAD. Desktop
    fetches and hard-resets its checkout to match. Desktop's live still
    holds Z (the old P content), untouched. Reports infer_base's pick
    under each variant (if any) and whether it is a genuine ancestor of
    the NEW HEAD -- and whether any variant's verdict is unsafe (silently
    proceeding as if it knew the true base when it does not)."""
    events = []
    Z = SEED_F + b"Z-content\n"
    edit_live(world.laptop, F_TARGET, Z)
    collect(world, "laptop"); commit(world, "laptop", "P: laptop edits to Z (this becomes P)")
    push(world, "laptop")
    pull(world, "desktop"); apply(world, "desktop")  # desktop syncs AT P == Z, for real

    Zp = SEED_F + b"Z-PRIME-rewritten-history\n"
    edit_live(world.laptop, F_TARGET, Zp)
    collect(world, "laptop")  # updates the checkout's WORKING TREE to Zp, uncommitted
    _git(world.laptop.checkout, "add", "-A")
    t = _tick()
    _git(world.laptop.checkout, "commit", "-q", "--amend",
        "-m", "P: amended -- the old P commit's sha no longer exists",
        env={"GIT_AUTHOR_DATE": str(t), "GIT_COMMITTER_DATE": str(t)})
    _git(world.laptop.checkout, "push", "-q", "--force", "origin", "main")
    _git(world.desktop.checkout, "fetch", "-q", "origin")
    _git(world.desktop.checkout, "reset", "-q", "--hard", "origin/main")
    events.append(snapshot(world, "desktop",
                           "P / SC-42: force-pushed history, old P unreachable; live still == old P (Z)"))
    return events


@scenario("Q")
def scenario_Q(world):
    """Q / SC-73, single probe: `git mv` the file between P and HEAD, WITH
    a content edit in the SAME commit. Live still has the OLD path's old
    content. Does `git log --follow` on the OLD path find the pre-rename
    content as a history candidate, and what does infer_base do with it?
    Run as a direct probe -- infer_base takes a repo_path directly, and
    which manifest entry (if any) would own the NEW path after a rename is
    a separate, unmodelled question from what --follow itself resolves."""
    events = []
    old_repo, new_repo = "dotclaude/Q-old.md", "dotclaude/Q-new.md"
    old_content = b"q original content\nline2\n"
    write_checkout(world, "laptop", old_repo, old_content)
    commit(world, "laptop", "Q: add Q-old.md (P)")
    push(world, "laptop")
    _git(world.laptop.checkout, "mv", old_repo, new_repo)
    (world.laptop.checkout / new_repo).write_bytes(old_content + b"post-rename-edit\n")
    _git(world.laptop.checkout, "add", "-A")
    t = _tick()
    _git(world.laptop.checkout, "commit", "-q", "-m", "Q: rename + edit in one commit",
        env={"GIT_AUTHOR_DATE": str(t), "GIT_COMMITTER_DATE": str(t)})
    push(world, "laptop")
    pull(world, "desktop")

    m = world.desktop
    theirs = git_show(m.checkout, new_repo, "HEAD")
    shas_old = git_log_shas(m.checkout, old_repo)  # does --follow find history under the OLD path?
    probe = {"follow_finds_old_path_history": bool(shas_old),
            "shas_under_old_path": [s[:7] for s in shas_old]}
    for vname, mod in world.variants.items():
        found = mod.merge.infer_base(m.checkout, old_repo, old_content, theirs)
        probe[vname] = ({"sha": found[1], "is_ancestor": git_is_ancestor(m.checkout, found[1], "HEAD")}
                        if found else None)
    events.append({"note": "Q / SC-73: rename+edit in one commit -- direct infer_base probe",
                  "machine": "desktop", "truth": {}, "probe": probe})
    return events


@scenario("R")
def scenario_R(world):
    """R / SC-11a: the ORDINARY case -- apply, then make a small edit to
    live. Arguably the single most common real-world shape (edit your
    config right after syncing it). Per the scenario-space DWP's Addendum
    2: V1/V2/V3 all exclude HEAD from candidacy (by position or by sha
    identity), so when the correct base IS HEAD itself (P == H, unmoved
    since the sync) and no OTHER commit happens to share HEAD's content,
    none of V1-V3 has anything to stand in for it -- they fall back to an
    older, wrong commit and falsely flag this as two-sided. V4 includes
    HEAD as an ordinary candidate and should pass this cleanly. Also runs
    `collect --dry-run` to show the concrete, everyday consequence."""
    events = []
    edit_live(world.laptop, F_TARGET, SEED_F + b"laptop-baseline\n")
    collect(world, "laptop"); commit(world, "laptop", "R: laptop baseline edit"); push(world, "laptop")
    pull(world, "desktop"); apply(world, "desktop")  # desktop's P == current HEAD, for real
    edit_live(world.desktop, F_TARGET, SEED_F + b"laptop-baseline\nsmall-post-sync-edit\n")
    ev = snapshot(world, "desktop",
                 "R / SC-11a: ordinary post-sync edit -- apply, then a small edit (the common case)")
    dry = collect(world, "desktop", dry_run=True)
    ev["collect_dry_run"] = {"copied": list(dry.copied)}
    events.append(ev)
    return events


# --------------------------------------------------------------------------
# Runner / report
# --------------------------------------------------------------------------

def _fmt_infer(infer) -> str:
    if infer is None:
        return "None"
    if infer == "in-sync":
        return "in-sync"
    anc = "ancestor" if infer["is_ancestor"] else "NOT-ancestor"
    return f"{infer['sha']} ({anc})"


_LABELS = {"file": "F  dotclaude/CLAUDE.md", "dir": "S  dotclaude/skills/S.md"}


_VARIANT_ORDER = ("pristine", "s1_corrected", "refined", "v4_full")
_EXTRA_KEYS = ("collect_dry_run", "apply_dry_run", "apply_dry_run_default",
              "apply_dry_run_sync_removals", "max_commits_30")


def print_event(ev: dict) -> None:
    print(f"\n--- {ev['note']}  [{ev['machine']}] ---")
    if "probe" in ev:
        for line in json.dumps(ev["probe"], indent=2, default=str).splitlines():
            print(f"  {line}")
    truth = ev.get("truth") or {}
    if truth:
        variant_names = [v for v in _VARIANT_ORDER if v in ev]
        for kind in truth:
            label = _LABELS.get(kind, f"{kind}  {ev[variant_names[0]][kind]['repo_path']}")
            t = truth[kind]
            print(f"  {label}")
            print(f"    truth: {t['verdict']}"
                 + (f" (base {t['base_sha']})" if t.get('base_sha') else ""))
            for vname in variant_names:
                g = ev[vname][kind]
                vd = verdict(ev, kind, vname)
                print(f"    [{vname:13}] status={g['status_bucket']:9} "
                     f"guard_both={str(g['guard_flagged_both']):5} "
                     f"infer={_fmt_infer(g['infer_base']):22} -> {vd}")
    for extra_key in _EXTRA_KEYS:
        if extra_key in ev:
            print(f"  {extra_key}: {ev[extra_key]}")


def run_all(only: str | None, json_out: Path | None) -> dict:
    scratch_root = Path(tempfile.gettempdir()) / "ccs-sim"
    print(f"[variants] preparing {', '.join(_VARIANT_PATCHERS)} under {scratch_root}")
    variants = _prepare_variants(scratch_root)
    for name, ns in variants.items():
        print(f"  {name}: merge.py = {ns.merge.__file__}")

    all_results: dict[str, list[dict]] = {}
    letters = [only] if only else list(SCENARIOS.keys())
    for letter in letters:
        fn = SCENARIOS[letter]
        print(f"\n{'=' * 70}\nSCENARIO {letter}: {fn.__doc__.strip().splitlines()[0]}\n{'=' * 70}")
        world = new_world(variants)
        try:
            events = fn(world)
        except Exception as e:
            print(f"  !!! scenario {letter} raised: {e!r}")
            raise
        for ev in events:
            print_event(ev)
        all_results[letter] = events
        shutil.rmtree(world.tmpdir, ignore_errors=True)

    if json_out:
        json_out.write_text(json.dumps(all_results, indent=1, default=str), encoding="utf-8")
        print(f"\n[json] written to {json_out}")
    return all_results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", default=None, help="run only this letter (e.g. B, Bp, D)")
    ap.add_argument("--json", default=None, help="write full structured results here")
    args = ap.parse_args(argv)
    run_all(args.scenario, Path(args.json) if args.json else None)


if __name__ == "__main__":
    main()
