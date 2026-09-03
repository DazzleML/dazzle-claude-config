"""Red-green audit for v0.5.20, in memory (no stash: another session shares
this repo's stash list). Three mechanisms, each neutralised by monkeypatching
the attribute the prompt looks up at call time; the prompt tests must go red
under the one they pin and green unpatched.

Run:  python -B tests/one-offs/thinking/redgreen_v0520.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from dazzle_claude_config import merge, render  # noqa: E402

T = "tests/test_loss_prompt.py"


def run(nodes):
    return pytest.main(["-q", "-p", "no:cacheprovider", "--no-header", "-rN",
                        *[str(ROOT / n.split("::")[0]) + ("::" + n.split("::")[1] if "::" in n else "")
                          for n in nodes]])


def _old_prompt(item, v):
    """The prompt as it was before v0.5.20 (merge.py at eb106a5)."""
    if not merge.interactive():
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


MECHANISMS = [
    ("M1 the old wording (v0.5.19's prompt body)",
     [f"{T}::test_the_prompt_says_what_it_knows_and_what_each_answer_does",
      f"{T}::test_plural_and_both_sides_read_right"],
     [("dazzle_claude_config.merge._ask_loss_on_console", _old_prompt)]),
    ("M2 fit() never cuts (the silent [:100] era, minus the slice)",
     [f"{T}::test_fit_cuts_to_the_real_budget_and_marks_it",
      f"{T}::test_a_narrow_terminal_marks_the_cut_and_names_the_way_to_see_it_whole"],
     [("dazzle_claude_config.render.fit", lambda text, indent=0, min_width=20: (text, False))]),
    ("M3 colour roles dropped (render.c returns plain text even when enabled)",
     [f"{T}::test_colour_roles_when_colour_is_on"],
     [("dazzle_claude_config.render.c", lambda name, text: text)]),
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
    green = run([T])
    print("\n=== red-green v0.5.20 ===")
    for name, verdict in report:
        print(f"  {verdict:36}  {name}")
    print(f"  {'GREEN' if green == 0 else 'RED (!)':36}  unpatched: {T}")
    ok = green == 0 and all(v.startswith("RED") for _n, v in report)
    print("RESULT:", "every mechanism anchored, suite green" if ok else "see above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
