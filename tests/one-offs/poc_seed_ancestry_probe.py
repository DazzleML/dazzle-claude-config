"""POC for issue #27's attribution-driven reseed hint, run against REAL data.

Question under test: is a live seeded file that predates a payload
restructure byte-equal (modulo EOL) to some HISTORICAL committed version
of its seed -- and can we detect that cheaply and correctly on Windows?

Measured inputs: aktuldjr's live ~/.claude/CLAUDE.md, hashed remotely over
WinRM (raw sha256 + LF-normalized sha256), 2026-08-25:

    raw : a2e6d528c17ebec3df0a53b23d2eff67cbe86131d9e0d32bc22af4f1666e3a84  (62,732 B)
    norm: 8faab1d06ab804e0b8519aeb969a853f45cf7a987ac5e8be4fa591256b660a2f  (61,716 B)

This script walks every committed version of dotclaude/CLAUDE.md in the
payload checkout, hashes each blob raw and LF-normalized, and reports which
commits match which hash. Expected finding (the design's premise): the raw
hash matches NOTHING (history stores LF; live files are CRLF -- a naive
`git hash-object` comparison would never fire), while the normalized hash
matches the pre-restructure seed version(s). Also measures the probe's
cost: number of history versions to hash.

Usage: python tests/one-offs/poc_seed_ancestry_probe.py [checkout_dir]
"""
import hashlib
import os
import subprocess
import sys

CHECKOUT = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "CCS_CHECKOUT_DIR", os.path.expanduser("~/claude/dazzle-claude-code-config"))
PATH = "dotclaude/CLAUDE.md"
LIVE_RAW = "a2e6d528c17ebec3df0a53b23d2eff67cbe86131d9e0d32bc22af4f1666e3a84"
LIVE_NORM = "8faab1d06ab804e0b8519aeb969a853f45cf7a987ac5e8be4fa591256b660a2f"


def git(*args) -> bytes:
    return subprocess.run(["git", "-C", CHECKOUT, *args],
                          capture_output=True, check=True).stdout


def main() -> int:
    commits = git("rev-list", "HEAD", "--", PATH).decode().split()
    print(f"{len(commits)} commits touched {PATH}")
    raw_hits, norm_hits = [], []
    for c in commits:
        blob = git("show", f"{c}:{PATH}")
        raw = hashlib.sha256(blob).hexdigest()
        norm = hashlib.sha256(blob.replace(b"\r\n", b"\n")).hexdigest()
        mark = []
        if raw == LIVE_RAW:
            raw_hits.append(c); mark.append("RAW-MATCH")
        if norm == LIVE_NORM:
            norm_hits.append(c); mark.append("NORM-MATCH")
        if mark:
            subj = git("log", "-1", "--format=%s", c).decode().strip()
            print(f"  {c[:8]}  {' '.join(mark)}  {subj[:60]}")
    head_blob = git("show", f"HEAD:{PATH}")
    head_norm = hashlib.sha256(head_blob.replace(b"\r\n", b"\n")).hexdigest()
    print(f"\nraw matches:  {len(raw_hits)} (expected 0 -- CRLF landmine is real if 0)")
    print(f"norm matches: {len(norm_hits)} (expected >=1, all pre-restructure)")
    print(f"live == CURRENT seed? {head_norm == LIVE_NORM} (expected False -- upstream replaced it)")
    verdict = (not raw_hits) and norm_hits and head_norm != LIVE_NORM
    print(f"\nPOC VERDICT: {'the #27 hint mechanism fires correctly on real data' if verdict else 'PREMISE BROKEN -- redesign before coding'}")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
