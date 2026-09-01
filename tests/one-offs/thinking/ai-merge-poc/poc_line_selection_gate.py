"""p0: one dual-touched hunk -> live `claude -p` -> validate().

See PREDICTION.md (pre-registered arms and pass criterion). Read-only toward
the repo: all run artifacts go to a temp workspace; this script writes nothing
under the project tree.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from dazzle_claude_config.merge import MergeItem, validate  # noqa: E402

BASE = """# Sync rules

Shared rules for every box.

## Deletions
Files are removed immediately when the payload retires them.

## Backups
Backups are kept for seven days.
"""

OURS = """# Sync rules

Shared rules for every box.

## Deletions
Files are staged to a recovery folder when the payload retires them.

## Backups
Backups are kept for seven days.
"""

THEIRS = """# Sync rules

Shared rules for every box.

## Deletions
Files are removed after an interactive confirmation when the payload retires them.

## Backups
Backups are kept for seven days.
"""

MARKER_BLOCK = re.compile(
    r"^<{7}[^\n]*\n(.*?)^\|{7}[^\n]*\n(.*?)^={7}\n(.*?)^>{7}[^\n]*\n",
    re.M | re.S,
)

PROMPT_A = """Resolve this three-way merge conflict. Write ONE harmonized line that
combines the intent of both sides into a single new sentence, then output ONLY
the replacement text for the conflict block (no markers, no commentary, no
code fences).

Conflict block (ours pane, then base pane, then theirs pane):

{block}
"""

PROMPT_B = """Resolve this three-way merge conflict. STRICT RULE: you may use ONLY
complete lines exactly as they appear in the panes below -- you may select
which lines to keep and in what order, but you may NEVER edit, merge, reword,
or invent a line. Output ONLY the replacement text for the conflict block (no
markers, no commentary, no code fences).

Conflict block (ours pane, then base pane, then theirs pane):

{block}
"""


def call_claude(prompt: str, cwd: Path, timeout: int = 180) -> tuple[str, float]:
    exe = shutil.which("claude")
    if not exe:
        sys.exit("claude CLI not on PATH")
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    t0 = time.time()
    r = subprocess.run([exe, "--output-format", "text", "-p", prompt],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, cwd=str(cwd), env=env)
    dt = time.time() - t0
    if r.returncode != 0:
        sys.exit(f"claude -p failed rc={r.returncode}: {r.stderr[:400]}")
    return r.stdout.strip(), dt


def run_arm(name: str, prompt_tmpl: str, ws: Path, merged_text: str,
            paths: dict[str, Path]) -> None:
    m = MARKER_BLOCK.search(merged_text)
    if not m:
        sys.exit("no conflict block found in merged text")
    block = m.group(0)
    prompt = prompt_tmpl.format(block=block)
    neutral = ws / f"neutral-{name}"
    neutral.mkdir(exist_ok=True)
    print(f"\n=== ARM {name}: prompt {len(prompt)} bytes ===")
    reply, dt = call_claude(prompt, neutral)
    print(f"  claude -p returned in {dt:.1f}s, {len(reply)} bytes:")
    for ln in reply.splitlines():
        print(f"    | {ln}")
    if not reply.endswith("\n"):
        reply += "\n"
    result = merged_text[:m.start()] + reply + merged_text[m.end():]
    out = ws / f"result-{name}.md"
    out.write_text(result, encoding="utf-8")

    item = MergeItem(entry=SimpleNamespace(target="poc.md", strategy="copy"),
                     rel="", live=paths["ours"], repo=paths["theirs"],
                     base=paths["base"])
    v = validate(item, out)
    fails = list(v.failures)
    print(f"  validate(): {'PASS (no failures)' if not fails else 'FAIL'}")
    for f in fails:
        print(f"    failure: {f}")


def main() -> None:
    ws = Path(tempfile.mkdtemp(prefix="ai-merge-poc-"))
    print(f"workspace: {ws}")
    paths = {}
    for nm, txt in (("base", BASE), ("ours", OURS), ("theirs", THEIRS)):
        p = ws / f"{nm}.md"
        p.write_text(txt, encoding="utf-8")
        paths[nm] = p
    r = subprocess.run(["git", "merge-file", "-p", "--diff3",
                        str(paths["ours"]), str(paths["base"]),
                        str(paths["theirs"])],
                       capture_output=True, text=True, encoding="utf-8")
    merged_text = r.stdout
    n_blocks = len(MARKER_BLOCK.findall(merged_text))
    print(f"git merge-file rc={r.returncode}, {n_blocks} conflict block(s)")
    if n_blocks != 1:
        sys.exit("fixture must produce exactly one conflict block")

    run_arm("A-harmonize", PROMPT_A, ws, merged_text, paths)
    run_arm("B-line-select", PROMPT_B, ws, merged_text, paths)
    print(f"\nartifacts kept in {ws}")


if __name__ == "__main__":
    main()
