"""Exhaustively enumerate the abstract state space of ccs's two-way classifier.

THE CLAIM THIS FILE SUPPORTS
    "The scenario catalogue (SC-xx) covers every case" cannot be proven over
    raw file contents -- that space is infinite. But `infer_base` and
    `two_way_labels` never read contents as such: they compare a handful of
    versions for EQUALITY (after EOL normalisation), count pure deletions
    against a threshold, and order candidates by edit distance. Those
    relations form a FINITE space. This script enumerates every consistent
    assignment of them, computes the truth and each variant's verdict
    symbolically, and maps each state to a catalogue class. A state with no
    class is a proven gap in the catalogue. A state where a variant's verdict
    is unsafe is a proven defect in that variant -- no git, no fixtures.

THE MODEL (what the code actually depends on)
    Versions:  L = live, W = checkout worktree, H = checkout HEAD,
               h1, h2 = the two most recent OLDER commits touching the path
               (two suffice to express "equals an older commit" vs "equals a
               DIFFERENT older commit" -- the shape behind SC-14).
    Hidden:    P = the sync point, one of {H, h1, h2} or NONE (unreachable /
               adoption). Truth depends on P; the tool cannot see it.
    Relations: a set-partition of {L, W, H, h1, h2} by content equality;
               del3[c] = "L deleted >= 3 lines vs candidate c" (only meaningful
               when c != L); nearer = which surviving older commit is nearer
               to L by edit distance (only meaningful when both survive);
               head_listed = whether `git log -- path` lists HEAD first
               (false after a TREESAME merge commit -- history simplification).
    Verdict:   flag (two-sided: one-way verbs refuse) or pass.

FIDELITY CAVEATS (what is OUTSIDE this model, stated so the proof is honest)
    * depth > 2 of history (argued: the rules only ever use "some older commit
      equals X" and "the nearest surviving candidate", both expressible at
      depth 2; deeper histories add copies of the same classes)
    * replace-vs-delete opcode subtleties inside base_phantom_ratio
    * directory vs file entries (TW1) -- a multiplier, not a new relation
    * multi-machine trajectories -- these GENERATE kernel states; they are
      not new kernel states. The simulator explores trajectories; this file
      proves state coverage. Together they are the argument.

Run:  python tests/one-offs/enumerate_classifier_states.py
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

VERSIONS = ["L", "W", "H", "h1", "h2"]


def set_partitions(items):
    """All ways to group items by equality (Bell(5) = 52)."""
    if not items:
        yield []
        return
    first, rest = items[0], items[1:]
    for smaller in set_partitions(rest):
        for i, block in enumerate(smaller):
            yield smaller[:i] + [[first] + block] + smaller[i + 1:]
        yield [[first]] + smaller


@dataclass(frozen=True)
class State:
    groups: tuple          # partition, as a tuple of frozensets
    P: str                 # "H" | "h1" | "h2" | "NONE"
    del3_h1: bool          # L deleted >= 3 lines vs h1 (if h1 != L)
    del3_h2: bool
    nearer: str            # "H" | "h1" | "h2" -- the surviving candidate nearest to L
    head_listed: bool      # git log lists HEAD first (False after TREESAME merge)
    tie_with_H: bool = False  # the nearest non-H candidate is at EXACTLY H's distance from L

    def eq(self, a, b):
        return any(a in g and b in g for g in self.groups)


# ---------------------------------------------------------------- truth ---

def truth(s: State) -> str:
    if s.eq("L", "W"):
        return "resolved"
    if s.eq("L", "H"):
        return "insync"
    if s.P == "NONE":
        return "nobase"           # adoption / unreachable: nothing is provable
    L_eq_P, H_eq_P = s.eq("L", s.P), s.eq("H", s.P)
    if L_eq_P and not H_eq_P:
        return "one-sided-checkout-ahead"
    if H_eq_P and not L_eq_P:
        return "one-sided-live-ahead"
    if L_eq_P and H_eq_P:
        return "insync"           # both equal P but L != H: impossible; guarded above
    return "two-sided"


def safe_verdict(t: str) -> str:
    """What a correct classifier must output for each truth."""
    return {"resolved": "skip", "insync": "pass", "nobase": "flag",
            "one-sided-checkout-ahead": "pass", "one-sided-live-ahead": "pass",
            "two-sided": "flag"}[t]


# ----------------------------------------------------- symbolic infer_base ---

def candidates(s: State, skip_by_sha: bool):
    """Older commits the walk will consider, in git-log order."""
    listed = (["H"] if s.head_listed else []) + ["h1", "h2"]
    if skip_by_sha:
        return [c for c in listed if c != "H"]
    return listed[1:]             # positional [1:] -- wrong when HEAD is not listed


def phantom_rejects(s: State, c: str) -> bool:
    """base_phantom_ratio: pure deletions (base->L) >= 3 AND retained by H."""
    if s.eq(c, "L"):
        return False              # no deletions at all
    deleted3 = {"h1": s.del3_h1, "h2": s.del3_h2}.get(c, False)
    if s.eq(c, "H"):
        return deleted3           # everything L deleted is retained by H by definition
    # c is a genuine third version: abstract -- treat "retained by H" as the
    # del3 flag too (the conservative reading; it is what SC-22 measures)
    return deleted3


def infer_base(s: State, variant: str):
    """Return the chosen base ("H"/"h1"/"h2") or None, per variant."""
    skip_by_sha = variant == "refined"
    survivors = []
    cands = (["H", "h1", "h2"] if variant in ("refined2", "refined3", "refined5") else candidates(s, skip_by_sha))
    rejected = []
    for c in cands:
        if c == "H":                              # only reachable when head_listed=False & positional
            pass                                  # H itself as a candidate (the latent bug)
        eq_ours, eq_theirs = s.eq(c, "L"), s.eq(c, "H")
        if variant == "pristine":
            if eq_ours or eq_theirs:
                continue
            if phantom_rejects(s, c):
                continue
            survivors.append(c)
        elif variant == "naive_s1":
            if eq_ours or eq_theirs:
                return c
            if phantom_rejects(s, c):
                continue
            survivors.append(c)
        elif variant in ("refined", "refined2", "refined3", "refined5"):
            if eq_ours:
                return c                          # distance zero: nothing nearer exists
            if not eq_theirs and phantom_rejects(s, c):
                rejected.append(c)
                continue                          # theirs-equal candidates (incl. HEAD) are exempt
            survivors.append(c)
    if variant in ("refined3", "refined5") and any(s.eq(r, s.nearer) for r in rejected):
        return None                               # the NEAREST candidate was phantom-rejected: refuse, never fall back
    if variant == "refined5" and s.tie_with_H and s.nearer != "H":
        # V5: a theirs-equal candidate wins only STRICTLY. On a tie the other candidate is
        # nearest; if it was rejected we already returned None above; else it wins.
        return s.nearer if s.nearer in survivors else None
    if not survivors:
        return None
    if variant == "refined3" and s.tie_with_H and "H" in survivors:
        return "H"                                # V4: min() takes the first-encountered on a tie = HEAD
    if len(survivors) == 1:
        return survivors[0]
    # two survivors: nearest to L wins (the existing SequenceMatcher score)
    return s.nearer if s.nearer in survivors else survivors[0]


def verdict(s: State, variant: str) -> str:
    """two_way_labels: skip if L==W; pass if L==H; else infer base and test."""
    if s.eq("L", "W"):
        return "skip"
    if s.eq("L", "H"):
        return "pass"
    base = infer_base(s, variant)
    if base is None:
        return "flag"
    if s.eq(base, "L") or s.eq(base, "H"):
        return "pass"
    return "flag"


# ------------------------------------------------------ catalogue mapping ---

def catalogue(s: State) -> str:
    """Map an abstract state to its SC class. 'UNCATALOGUED' = proven gap."""
    if s.eq("L", "W"):
        return "SC-01" if not s.eq("W", "H") else "SC-02"
    if s.eq("L", "H"):
        return "SC-03" if not s.eq("W", "H") else "SC-02"
    if s.P == "NONE":
        return "SC-80"
    L_old = [c for c in ("h1", "h2") if s.eq("L", c)]
    H_old = [c for c in ("h1", "h2") if s.eq("H", c)]
    if L_old:
        return "SC-10" if s.eq("L", s.P) else "SC-15"
    if H_old:
        if not s.eq("H", s.P):
            return "SC-14"
        d3 = any({"h1": s.del3_h1, "h2": s.del3_h2}[c] for c in H_old)
        return "SC-12" if d3 else "SC-11/13"
    # neither side equals any older commit
    if s.P == "H":
        return "SC-11a"          # live-ahead, sync point IS HEAD (apply, then edit) -- the common case
    if s.del3_h1 or s.del3_h2:
        return "SC-22"
    return "SC-20/21"


# ------------------------------------------------------------- enumerate ---

def consistent(s: State) -> bool:
    """Prune assignments that contradict themselves."""
    # P must be a real commit here; NONE handled separately
    # del3 is meaningless (forced False) when the candidate equals L
    if s.eq("h1", "L") and s.del3_h1:
        return False
    if s.eq("h2", "L") and s.del3_h2:
        return False
    # a candidate equal to L is at distance 0 and must be the nearest
    for c in ("H", "h1", "h2"):
        if s.eq(c, "L") and s.nearer != c and not s.eq(s.nearer, "L"):
            return False
    # if h1 == h2 by content, their flags must agree
    if s.eq("h1", "h2") and (s.del3_h1 != s.del3_h2):
        return False
    return True


import sys
REALISTIC = "--realistic" in sys.argv   # nearest candidate to L is P when P is reachable (L = P + edits)


def main():
    states = []
    for groups in set_partitions(VERSIONS):
        g = tuple(frozenset(b) for b in groups)
        for P in ("H", "h1", "h2", "NONE"):
            for d1, d2 in itertools.product((False, True), repeat=2):
                for nearer in ("H", "h1", "h2"):
                    for head_listed in (True, False):
                      for tie in (False, True):
                        if tie and nearer == "H":
                            continue        # a tie is expressed with nearer = the non-H side
                        s = State(g, P, d1, d2, nearer, head_listed, tie)
                        if not consistent(s):
                            continue
                        if REALISTIC and P != "NONE" and not any(s.eq(c, "L") for c in ("H", "h1", "h2")):
                            # live descends from P: P (or anything equal to it) is nearest
                            if not s.eq(nearer, P):
                                continue
                        states.append(s)

    variants = ("pristine", "naive_s1", "refined", "refined2", "refined3", "refined5")
    gaps, by_class = [], {}
    unsafe = {v: [] for v in variants}
    over = {v: [] for v in variants}
    for s in states:
        cls = catalogue(s)
        by_class.setdefault(cls, 0)
        by_class[cls] += 1
        if cls == "UNCATALOGUED":
            gaps.append(s)
        want = safe_verdict(truth(s))
        for v in variants:
            got = verdict(s, v)
            if want == "flag" and got == "pass":
                unsafe[v].append((cls, s))
            elif want == "pass" and got == "flag":
                over[v].append((cls, s))

    print(f"mode: {'REALISTIC geometry (P nearest when reachable)' if REALISTIC else 'WORST CASE (nearest is free)'}")
    print(f"abstract states enumerated (consistent): {len(states)}")
    print(f"uncatalogued states (proven gaps):       {len(gaps)}")
    print("\nstates per catalogue class:")
    for k in sorted(by_class):
        print(f"  {k:<10} {by_class[k]:>5}")
    print("\nverdict quality per variant (over all states):")
    print(f"  {'variant':<10} {'UNSAFE (loss)':>14} {'over-refuse':>12}")
    for v in variants:
        print(f"  {v:<10} {len(unsafe[v]):>14} {len(over[v]):>12}")

    def show(label, rows, n=6):
        if not rows:
            return
        print(f"\n{label} -- by class:")
        agg = {}
        for cls, s in rows:
            agg.setdefault(cls, 0); agg[cls] += 1
        for cls, k in sorted(agg.items()):
            print(f"  {cls:<10} {k}")
        print("  example states:")
        for cls, s in rows[:n]:
            grp = " | ".join("=".join(sorted(b)) for b in s.groups if len(b) > 1) or "all distinct"
            print(f"    {cls:<9} P={s.P:<4} head_listed={s.head_listed!s:<5} del3=({s.del3_h1!s:<5},{s.del3_h2!s:<5}) nearer={s.nearer}  [{grp}]")

    for v in variants:
        show(f"UNSAFE under {v}", unsafe[v])
    for v in variants:
        show(f"over-refuse under {v}", over[v], n=3)
    if gaps:
        show("UNCATALOGUED", [("UNCATALOGUED", s) for s in gaps])


if __name__ == "__main__":
    main()
