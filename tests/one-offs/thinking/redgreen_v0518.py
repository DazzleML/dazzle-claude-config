"""Red-green audit for v0.5.18, in memory -- no stash, no checkout (another
session shares this repo's stash list; the v0.5.17 audit used this pattern).

Each mechanism below neutralises ONE production behaviour by monkeypatching
the module attribute the code looks up at call time, then runs the new test
files in-process and expects failures. Then everything runs unpatched and
must be green. A test that fails under a mechanism is an ANCHOR for it; one
that passes either way is an invariant guard (still worth having, but not
proof of that mechanism).

Run:  python -B tests/one-offs/thinking/redgreen_v0518.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from dazzle_claude_config import cli, seeddecisions  # noqa: E402

POSITIONAL = "tests/test_positional_path.py"
SEEDS = "tests/test_one_actionable_set.py"


def run(files: list[str]) -> int:
    return pytest.main(["-q", "-p", "no:cacheprovider", "-x", "--no-header",
                        "-rN", *[str(ROOT / f) for f in files]])


MECHANISMS = [
    # (name, targets, patches)  -- patches are (dotted attribute, replacement)
    ("M1 the positional resolver finds nothing (resolve_pair None, suffix never matches)",
     [POSITIONAL],
     [("dazzle_claude_config.cli._resolve_pair", lambda *a, **k: None),
      ("dazzle_claude_config.cli._suffix_match", lambda *a, **k: False)]),
    ("M2 no seed state counts as settled (SETTLED empty)",
     [SEEDS],
     [("dazzle_claude_config.seeddecisions.SETTLED", frozenset())]),
    ("M3 merge sees no seed findings at all (findings -> empty)",
     [SEEDS],
     [("dazzle_claude_config.seeddecisions.findings", lambda *a, **k: ([], []))]),
]


def main() -> int:
    report = []
    for name, files, patches in MECHANISMS:
        ctxs = [mock.patch(target, new) for target, new in patches]
        for c in ctxs:
            c.start()
        try:
            rc = run(files)
        finally:
            for c in reversed(ctxs):
                c.stop()
        verdict = "RED (anchored)" if rc != 0 else "GREEN under mutant -- NOT an anchor"
        report.append((name, verdict))
    green = run([POSITIONAL, SEEDS])
    print("\n=== red-green v0.5.18 ===")
    for name, verdict in report:
        print(f"  {verdict:36}  {name}")
    print(f"  {'GREEN' if green == 0 else 'RED (!)':36}  unpatched: both files")
    ok = green == 0 and all(v.startswith("RED") for _n, v in report)
    print("RESULT:", "every mechanism anchored, suite green" if ok else "see above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
