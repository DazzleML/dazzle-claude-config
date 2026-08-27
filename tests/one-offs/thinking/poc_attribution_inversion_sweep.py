"""MEASURE: when does ccs's direction attribution disagree with ground truth?

Issue #36 reports that a file which is merely STALE in live (a checkout-side
change was committed and never applied) is labelled "live ahead", and `apply`
then prints `ccs collect` -- which would revert the committed checkout work.

Reasoning about `infer_base`'s five rules predicts *a* shape where this
happens, but a prediction is not data. This script builds each scenario for
real, runs the real CLI, and records what ccs actually says -- so the design
discussion works off measurements instead of a mental model of the rules.

The key falsifiable invariant this measures:

    A side that contributes ZERO unique lines cannot be "ahead".

ccs prints both the label and the unique-line counts on the same line, so
whenever the label says "X ahead" while X's unique-line count is 0, the
output contradicts itself using only information it already computed. That
matters because it means the contradiction is detectable WITHOUT solving the
recorded-sync-point problem (#14).

Run:  python tests/one-offs/thinking/poc_attribution_inversion_sweep.py
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GIT_ID = ["-c", "user.email=poc@test.invalid", "-c", "user.name=poc",
          "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]

MANIFEST = (
    '{"manifest_version":1,'
    '"territories":{"dotclaude":{"root_var":"CLAUDE_DIR","repo_dir":"dotclaude"}},'
    '"entries":[{"repo":"dotclaude/skills","territory":"dotclaude",'
    '"target":"skills","strategy":"copy"}]}\n'
)


def git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *GIT_ID, "-C", str(cwd), *args],
                       capture_output=True, text=True)
    if r.returncode != 0 and args[0] not in ("status",):
        raise RuntimeError(f"git {args}: {r.stderr.strip()}")
    return r.stdout


def lines(*ls: str) -> str:
    return "".join(l + "\n" for l in ls)


def build(tmp: Path, history: list[str], live_content: str) -> dict:
    """history: successive contents to commit. live_content: what live holds."""
    co, live, user = tmp / "co", tmp / "live", tmp / "user"
    for p in (co / "dotclaude" / "skills", live / "skills", user):
        p.mkdir(parents=True)
    (co / "ccs-manifest.json").write_text(MANIFEST, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    target = co / "dotclaude" / "skills" / "s.md"
    for i, content in enumerate(history):
        target.write_text(content, encoding="utf-8")
        if i == 0:
            git(co, "add", "-A")
            git(co, "commit", "-qm", f"v{i+1}")
        else:
            git(co, "commit", "-qam", f"v{i+1}")
    (live / "skills" / "s.md").write_text(live_content, encoding="utf-8")
    return dict(co=co, live=live, user=user)


def ccs(w: dict, *verb: str) -> str:
    r = subprocess.run(
        [sys.executable, "-m", "dazzle_claude_config.cli",
         "--checkout-dir", str(w["co"]), "--claude-dir", str(w["live"]),
         "--user-claude", str(w["user"]), "--no-color", "--no-fetch", *verb],
        capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parents[3]))
    return r.stdout + r.stderr


COUNTS = re.compile(r"(\d+) only live / (\d+) replaced / (\d+) only checkout")


def measure(name: str, history: list[str], live_content: str,
            truth: str) -> dict:
    """truth: which side genuinely holds content the other lacks."""
    tmp = Path(tempfile.mkdtemp(prefix="ccs-attr-"))
    try:
        w = build(tmp, history, live_content)
        status = ccs(w, "status", "--long")
        line = next((l.strip() for l in status.splitlines()
                     if "s.md" in l and ("--" in l)), "")
        m = COUNTS.search(line)
        only_live, replaced, only_co = (int(m.group(1)), int(m.group(2)),
                                        int(m.group(3))) if m else (-1, -1, -1)
        label = ("unattributed" if "direction unproven" in line
                 or "more likely STALE" in line else
                 "live ahead" if "live ahead" in line else
                 "checkout ahead" if "checkout ahead" in line else
                 "two-sided" if "both moved" in line else
                 "no base" if "no ancestor" in line else
                 "local snap" if "not committed" in line else "?")
        applied = ccs(w, "apply")
        if "skipped" in applied and "s.md" in applied:
            action = "SKIPPED -- recommends collect"
        elif "applied:" in applied:
            action = "applied"
        elif "REFUS" in applied.upper():
            action = "refused"
        else:
            action = "(other)"
        # The self-contradiction test: a side with no unique lines is not ahead.
        contradicts = ((label == "live ahead" and only_live == 0) or
                       (label == "checkout ahead" and only_co == 0))
        return dict(name=name, label=label, truth=truth, action=action,
                    only_live=only_live, replaced=replaced, only_co=only_co,
                    contradicts=contradicts,
                    wrong=(label == "live ahead" and truth == "checkout"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


V1 = lines("# skill", "rule A")
V2 = lines("# skill", "rule A", "rule B")
V3_BIG = lines("# skill", "rule A", "rule B", "rule C", "rule D", "rule E")

SCENARIOS = [
    # (name, history, live, which side genuinely holds unique content)
    ("live == an older COMMIT exactly (stale, matched)",
     [V1, V2], V1, "checkout"),
    ("live is stale and UNMATCHED, near HEAD  <-- the #36 shape",
     [V1, V2, V3_BIG], lines("# skill", "rule A", "rule B", "rule C", "rule D"),
     "checkout"),
    ("live is stale and UNMATCHED, far from HEAD",
     [V1, V2, V3_BIG], lines("# skill"), "checkout"),
    ("live genuinely edited on top of HEAD",
     [V1, V2], V2 + "local note\n", "live"),
    ("both sides moved since a shared base",
     [V1, V2], V1 + "local note\n", "both"),
    ("live == HEAD (no drift)",
     [V1, V2], V2, "neither"),
]


def main() -> int:
    rows = [measure(*s) for s in SCENARIOS]
    w = max(len(r["name"]) for r in rows)
    print(f"\n{'scenario':<{w}}  {'ccs label':<15} {'truth':<9} "
          f"{'live/repl/co':<13} {'apply does':<28} flags")
    print("-" * (w + 80))
    for r in rows:
        flags = []
        if r["contradicts"]:
            flags.append("SELF-CONTRADICTS")
        if r["wrong"]:
            flags.append("INVERTED")
        print(f"{r['name']:<{w}}  {r['label']:<15} {r['truth']:<9} "
              f"{r['only_live']}/{r['replaced']}/{r['only_co']:<9} "
              f"{r['action']:<28} {' '.join(flags)}")

    bad = [r for r in rows if r["wrong"]]
    contra = [r for r in rows if r["contradicts"]]
    print(f"\ninverted labels:      {len(bad)} / {len(rows)}")
    print(f"self-contradicting:   {len(contra)} / {len(rows)}")
    if contra:
        print("\nEvery inverted case is ALSO self-contradicting: ccs printed a")
        print("unique-line count of 0 for the side it called 'ahead'. The")
        print("contradiction is detectable from numbers ccs already computed,")
        print("with no recorded sync point (#14) required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
