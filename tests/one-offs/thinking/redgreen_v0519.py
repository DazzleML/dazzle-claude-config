"""Red-green audit for v0.5.19, in memory -- same pattern as redgreen_v0518.py
(no stash: another session shares this repo's stash list).

Mechanisms neutralised one at a time by monkeypatching the attribute the code
looks up at call time; the four new tests must go red under the mechanism
they pin, and green unpatched.

Run:  python -B tests/one-offs/thinking/redgreen_v0519.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from dazzle_claude_config import cli  # noqa: E402

T = "tests/test_positional_path.py"
NEW = [
    f"{T}::test_diff_resolves_a_seed_entry_too",
    f"{T}::test_one_path_resolves_to_one_label_for_all_four_verbs",
    f"{T}::test_a_tag_gated_file_named_at_merge_says_not_for_this_box",
    f"{T}::test_the_not_found_sentence_is_defined_once",
]


def run(nodes: list[str]) -> int:
    return pytest.main(["-q", "-p", "no:cacheprovider", "--no-header", "-rN",
                        *[str(ROOT / n) if "::" not in n else str(ROOT / n.split("::")[0]) + "::" + n.split("::")[1]
                          for n in nodes]])


def _no_manifest_step(all_diffs, want, **kw):
    """The resolver as it was in v0.5.18: the manifest step removed."""
    return cli.__dict__["_resolve_pair_original"](all_diffs, want)


MECHANISMS = [
    ("M1 the resolver's manifest step removed (v0.5.18 behaviour)",
     NEW[0:2],
     [("dazzle_claude_config.cli._resolve_pair", _no_manifest_step)]),
    ("M2 the gated-entry explanation never fires (_gated_matches -> [])",
     NEW[2:3],
     [("dazzle_claude_config.cli._gated_matches", lambda *a, **k: [])]),
]


def main() -> int:
    cli._resolve_pair_original = cli._resolve_pair
    report = []
    for name, nodes, patches in MECHANISMS:
        ctxs = [mock.patch(target, new) for target, new in patches]
        for c in ctxs:
            c.start()
        try:
            rc = run(nodes)
        finally:
            for c in reversed(ctxs):
                c.stop()
        report.append((name, "RED (anchored)" if rc != 0 else "GREEN under mutant -- NOT an anchor"))
    green = run(NEW)
    print("\n=== red-green v0.5.19 ===")
    for name, verdict in report:
        print(f"  {verdict:36}  {name}")
    print(f"  {'GREEN' if green == 0 else 'RED (!)':36}  unpatched: the four new tests")
    print("  (guard by construction)                 test_the_not_found_sentence_is_defined_once "
          "is a source scan -- it pins the count, not a runtime path")
    ok = green == 0 and all(v.startswith("RED") for _n, v in report)
    print("RESULT:", "every mechanism anchored, new tests green" if ok else "see above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
