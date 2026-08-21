"""Build the scratch world for the v0.4.1 human checklist.

    python tests/checklists/helpers/make_scratch_v041.py <dir>

Creates <dir>/checkout (a git repo with history) and <dir>/live, <dir>/user
(the two live territories), so every checklist step runs against throwaway
state and never touches ~/.claude or ~/claude. Safe to re-run: it refuses
if <dir> already exists.

The world, per file (all under the `dotclaude` territory):

  CLAUDE.md   single-file entry; checkout ahead (live == seed commit)
  s/A.md      checkout ahead  (live == seed commit)
  s/B.md      live ahead      (checkout untouched since seed)
  t/C.md      two-sided       (both moved since the seed) -- its own entry, so
              `--only dotclaude/s` can show the direction skips while the
              unscoped verbs still refuse on it
  s/D.md      local snapshot  (exists in the checkout, never committed)
  s/E.md      checkout ahead, but live is CRLF (editor-style line endings)
  s/F.md      identical on both sides, CRLF in live / LF in the checkout
  s/emoji.md  live ahead; content carries a non-cp1252 character
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

GIT = ["git", "-c", "user.name=ccs-checklist", "-c", "user.email=ccs@example.invalid",
       "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]


def run(co: Path, *args: str) -> None:
    r = subprocess.run([*GIT, "-C", str(co), *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed:\n{r.stderr}")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1]).resolve()
    if root.exists():
        sys.exit(f"{root} already exists -- pick a fresh directory (nothing was touched)")
    co, live, user = root / "checkout", root / "live", root / "user"
    (co / "dotclaude" / "s").mkdir(parents=True)
    (co / "dotclaude" / "t").mkdir()
    (live / "s").mkdir(parents=True)
    (live / "t").mkdir()
    user.mkdir()

    subprocess.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    manifest = {
        "manifest_version": 1,
        "territories": {"dotclaude": {"root_var": "CLAUDE_DIR", "repo_dir": "dotclaude"}},
        "entries": [
            {"repo": "dotclaude/CLAUDE.md", "territory": "dotclaude",
             "target": "CLAUDE.md", "strategy": "copy"},
            {"repo": "dotclaude/s", "territory": "dotclaude",
             "target": "s", "strategy": "copy"},
            {"repo": "dotclaude/t", "territory": "dotclaude",
             "target": "t", "strategy": "copy"},
        ],
    }
    (co / "ccs-manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")

    def w(rel: str, text: str, base: Path = co / "dotclaude") -> None:
        (base / rel).write_bytes(text.encode("utf-8"))

    # seed commit -----------------------------------------------------------
    seed = {
        "CLAUDE.md": "# memory\n\n- rule one\n- rule two\n",
        "s/A.md": "a1\na2\na3\n",
        "s/B.md": "b1\nb2\n",
        "t/C.md": "c1\nc2\nc3\nc4\n",
        "s/E.md": "e1\ne2\n",
        "s/F.md": "f1\nf2\n",
        "s/emoji.md": "# tips\n",
    }
    for rel, text in seed.items():
        w(rel, text)
    run(co, "add", "-A")
    run(co, "commit", "-qm", "seed")

    # the checkout moves on (the "other machine" committed) ------------------
    w("CLAUDE.md", seed["CLAUDE.md"] + "- rule three (from the other machine)\n")
    w("s/A.md", seed["s/A.md"] + "a4 (from the other machine)\n")
    w("t/C.md", "c1\nc2-edited-in-checkout\nc3\nc4\n")
    w("s/E.md", seed["s/E.md"] + "e3 (from the other machine)\n")
    run(co, "add", "-A")
    run(co, "commit", "-qm", "the other machine advances CLAUDE.md, A, C, E")

    # an uncommitted local snapshot in the checkout --------------------------
    w("s/D.md", "d-old-snapshot\n")

    # the live tree -----------------------------------------------------------
    w("CLAUDE.md", seed["CLAUDE.md"], live)                       # == seed
    w("s/A.md", seed["s/A.md"], live)                             # == seed
    w("s/B.md", seed["s/B.md"] + "b3 (edited here)\n", live)      # live ahead
    w("t/C.md", "c1\nc2\nc3\nc4-edited-in-live\n", live)           # two-sided
    w("s/D.md", "d-newer-live\n", live)                           # newer than the snapshot
    (live / "s" / "E.md").write_bytes(seed["s/E.md"].replace("\n", "\r\n").encode())  # == seed, CRLF
    (live / "s" / "F.md").write_bytes(seed["s/F.md"].replace("\n", "\r\n").encode())  # identical, CRLF
    (live / "s" / "emoji.md").write_bytes("# tips \U0001f4a1\n".encode("utf-8"))   # live ahead, emoji

    print(f"scratch world ready under {root}")
    print(f"  checkout: {co}")
    print(f"  live:     {live}")
    print(f"  user:     {user}")
    print("run every ccs command with:")
    print(f'  ccs --checkout-dir "{co}" --claude-dir "{live}" --user-claude "{user}" --no-color <verb>')
    return 0


if __name__ == "__main__":
    sys.exit(main())
