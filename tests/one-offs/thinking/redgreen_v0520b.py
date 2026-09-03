"""Red-green audit for the side-aware prompt and the marked failure excerpts
(the 2026-09-03 eyes-step follow-up). In memory; no stash.

Run:  python -B tests/one-offs/thinking/redgreen_v0521.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from dazzle_claude_config import merge  # noqa: E402

T = "tests/test_loss_prompt.py"
M = "tests/test_merge.py"


def run(nodes):
    return pytest.main(["-q", "-p", "no:cacheprovider", "--no-header", "-rN",
                        *[str(ROOT / n.split("::")[0]) + ("::" + n.split("::")[1] if "::" in n else "")
                          for n in nodes]])


MECHANISMS = [
    ("M1 the excerpt cut goes silent again (no marker)",
     [f"{T}::test_the_failure_strings_use_the_same_words_and_mark_their_cut"],
     [("dazzle_claude_config.merge._excerpt", lambda line, n: line[:n])]),
    ("M2 sides named ours/theirs again in the failure strings",
     [f"{M}::test_result_identical_to_ours_is_rejected_without_probes",
      f"{M}::test_result_identical_to_theirs_is_rejected_without_probes",
      f"{T}::test_the_failure_strings_use_the_same_words_and_mark_their_cut"],
     [("dazzle_claude_config.merge._side_name", lambda side: side)]),
]


def main() -> int:
    report = []
    for name, nodes, patches in MECHANISMS:
        ctxs = [mock.patch(t, n) for t, n in patches]
        for c in ctxs:
            c.start()
        try:
            rc = run(nodes)
        finally:
            for c in reversed(ctxs):
                c.stop()
        report.append((name, "RED (anchored)" if rc != 0 else "GREEN under mutant -- NOT an anchor"))
    # M3: the side-aware closing pair. There is no attribute to patch -- the
    # branching is inline -- so the anchor is demonstrated by the tests
    # themselves: test_plural_and_both_sides_read_right asserts the mixed
    # sentence AND the absence of the old one, and test_the_prompt_says_...
    # asserts the ours-only sentence; both were written red against the
    # pre-fix body (the v0.5.20 wording) before the fix was applied.
    green = run([T, f"{M}::test_result_identical_to_ours_is_rejected_without_probes",
                 f"{M}::test_result_identical_to_theirs_is_rejected_without_probes"])
    print("\n=== red-green v0.5.20b (side-aware prompt, marked excerpts; amended into v0.5.20) ===")
    for name, verdict in report:
        print(f"  {verdict:36}  {name}")
    print("  (anchored by construction)              M3 side-aware closing pair: the mixed and ours-only "
          "tests fail on the v0.5.20 body (they assert sentences it never printed)")
    print(f"  {'GREEN' if green == 0 else 'RED (!)':36}  unpatched")
    ok = green == 0 and all(v.startswith("RED") for _n, v in report)
    print("RESULT:", "every mechanism anchored, green unpatched" if ok else "see above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
