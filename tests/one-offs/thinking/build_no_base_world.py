"""Build a scratch world where `ccs merge --no-launch` reaches the no-base loss
prompt -- for a person to read it on a real terminal (colour needs a TTY, so
the final command is theirs to run).

Shape: a checkout with ONE commit whose F.md shares no ancestor with the live
F.md (live was never committed anywhere), so infer_base finds nothing and the
merge is two-way. The workspace is then seeded once (headless) and the
resolution is edited to drop a line the live file has -- the situation the
prompt exists for. The next `ccs merge --no-launch` asks.

Usage:  python tests/one-offs/thinking/build_no_base_world.py
Then run the printed commands in your own terminal.
"""
from __future__ import annotations

import os
import subprocess as sp
import sys
from pathlib import Path

ROOT = Path(os.environ.get("TEMP", "/tmp")) / "ccs-prompt-eyes"
GIT_ID = ["-c", "user.email=t@t.invalid", "-c", "user.name=t", "-c", "commit.gpgsign=false"]


def main() -> int:
    if ROOT.exists():
        print(f"{ROOT} exists -- remove it first (dz safedel), or pick a fresh box")
        return 2
    co = ROOT / "checkout"; (co / "dotclaude").mkdir(parents=True)
    live = ROOT / "live"; live.mkdir()
    user = ROOT / "user"; user.mkdir()

    def git(*a):
        r = sp.run(["git", *GIT_ID, "-C", str(co), *a], capture_output=True, text=True)
        assert r.returncode == 0, f"git {a}: {r.stderr}"

    sp.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    (co / "ccs-manifest.json").write_text(
        '{"manifest_version":1,'
        '"territories":{"dotclaude":{"root_var":"CLAUDE_DIR","repo_dir":"dotclaude"}},'
        '"entries":[{"repo":"dotclaude/F.md","territory":"dotclaude","target":"F.md","strategy":"copy"}]}',
        encoding="utf-8")
    # theirs: the only commit; nothing in history equals live -> no base
    (co / "dotclaude/F.md").write_text(
        "# Rules\n\nShared rules.\n\n## Backups\nKept for seven days.\n\n## Deletions\n"
        "Files are removed after an interactive confirmation when retired.\n", encoding="utf-8")
    git("add", "-A"); git("commit", "-qm", "theirs")
    # ours: never committed anywhere; carries one long line of its own
    ours = ("# Rules\n\nShared rules.\n\n## Backups\nKept for seven days.\n\n## Deletions\n"
            "Files are staged to a recovery folder when retired, and the recovery folder is "
            "swept only after a person confirms the sweep on the box that owns it.\n")
    (live / "F.md").write_text(ours, encoding="utf-8")

    env = {**os.environ, "CCS_CHECKOUT_DIR": str(co), "CLAUDE_CONFIG_DIR": str(live)}
    base = ["ccs", "--no-fetch", "--no-color", "--user-claude", str(user)]
    # seed the workspace headlessly (a fresh seed is never asked about)
    sp.run([*base, "merge", "--no-launch"], env=env, capture_output=True, text=True)
    merged = user / "merge" / "ccs" / "F.md.merged"
    if not merged.is_file():
        print("no .merged was seeded -- the world is not what this script expects"); return 2
    # "resolve" it by hand: drop the long line ours has (the case the prompt is for)
    text = merged.read_text(encoding="utf-8")
    dropped = [ln for ln in text.splitlines() if ln.startswith("Files are staged")]
    if not dropped:
        print("the seed does not carry the live line; seeding rule changed?"); return 2
    merged.write_text("\n".join(ln for ln in text.splitlines() if not ln.startswith("Files are staged")) + "\n",
                      encoding="utf-8")
    # headless check: piped stdin => the prompt refuses silently and reports NOT INSTALLED
    r = sp.run([*base, "merge", "--no-launch"], env=env, capture_output=True, text=True)
    ok = "NOT INSTALLED F.md" in r.stdout and "dropped" in r.stdout
    print("prepared:", ROOT)
    print("headless check (piped, so no prompt):", "reaches the gate" if ok else "UNEXPECTED:\n" + r.stdout)
    print("\nRun these in YOUR terminal (cmd.exe), then answer N:\n")
    print(f'  set CCS_CHECKOUT_DIR={co}')
    print(f'  set CLAUDE_CONFIG_DIR={live}')
    print(f'  ccs --no-fetch --user-claude "{user}" merge --no-launch')
    print("\nPowerShell:  $env:CCS_CHECKOUT_DIR=...; $env:CLAUDE_CONFIG_DIR=...; same ccs line.")
    print("\nAfterwards:  set CCS_CHECKOUT_DIR=   and   set CLAUDE_CONFIG_DIR=   (unset them),")
    print(f"             then dz safedel \"{ROOT}\"")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
