"""Build the scratch world for the v0.4.3 adoption-merge checklist.

    python tests/checklists/helpers/make_scratch_v043_adopt.py <dir> [--history | --two-way]

Creates <dir>/checkout (a git repo), <dir>/live, <dir>/user, <dir>/ancestor.md
and <dir>/home-repo (a second git repo holding the ancestor at
.claude/CLAUDE.md, for --base-from). Refuses if <dir> exists. Prints the
`ccs` prefix to use.

Shapes (the synthetic twin of a real forked production file):

  default    the checkout's history holds NO ancestor of the live file -- its
             first commit is already a post-fork payload revision (a sibling
             the inferred-base rules reject), HEAD is the current payload.
             The live file is the box's copy: its own section on top, one
             shared rule dropped, and the sections upstream retired kept.
             Use with --base-file <dir>/ancestor.md.
  --history  the first commit IS the ancestor, so the base is inferred and an
             ordinary merge honours upstream's retirement (HV.5).
  --two-way  one commit, no ancestor anywhere (the no-base ask, Section 1).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

GIT = ["git", "-c", "user.name=ccs-checklist", "-c", "user.email=ccs@example.invalid",
       "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]

NL = "\n"
RULES = NL.join(["## Rules", "- rule one: commit often", "- rule two: never force-push shared branches",
                 "- rule three: tests before merge"]) + NL
RULES_T = NL.join(["## Rules", "- rule one: commit often",
                   "- rule two: force-push only your own branches, with --force-with-lease",
                   "- rule three: tests before merge"]) + NL
WIN = NL.join(["## Windows notes", "- use PowerShell for junctions", "- codepage 437 breaks unicode"]) + NL
GLOSS = NL.join(["## Glossary", "- payload: the shared repo", "- box: one machine"]) + NL
PM = NL.join(["## Postmortem commands", "- /fullpostmortem after big work", "- /minipostmortem for a bug"]) + NL
ED = NL.join(["## Editing workflow", "- write to tests/one-offs first", "- never heredocs with backslashes"]) + NL
FLEET = NL.join(["## Fleet notes", "- four machines share one payload",
                 "- status fetches before it says in sync", "- apply never deletes in place"]) + NL
PORT = NL.join(["## Port allocation on this box", "- 8080 api", "- 8443 tls termination",
                "- 5432 postgres, local only"]) + NL
REMOTE = NL.join(["## Remote awareness", "- the checkout is fetched first"]) + NL
SEL = NL.join(["## Selective sync", "- --only scopes a run"]) + NL
HEAD_ = "# Config" + NL + NL

BASE = HEAD_ + RULES + NL + WIN + NL + GLOSS + NL + PM + NL + ED
THEIRS = HEAD_ + RULES_T + NL + FLEET + NL + WIN + NL + PM + NL + REMOTE
SIBLING = HEAD_ + RULES + NL + FLEET + NL + WIN + NL + PM + NL + SEL
OURS = (HEAD_ + PORT + NL + RULES.replace("- rule three: tests before merge" + NL, "")
        + NL + WIN + NL + GLOSS + NL + PM + NL + ED)

# --history: a simpler triple where the ancestor is commit 1
H_BASE = HEAD_ + RULES + NL + "## Legacy notes" + NL + "- the foo service restarts nightly" + NL
H_THEIRS = HEAD_ + RULES + NL + REMOTE
H_OURS = HEAD_ + RULES + "- rule four: added on this box" + NL + NL + "## Legacy notes" + NL + "- the foo service restarts nightly" + NL

MANIFEST = ('{"manifest_version":1,'
            '"territories":{"dotclaude":{"root_var":"CLAUDE_DIR","repo_dir":"dotclaude"}},'
            '"entries":[{"repo":"dotclaude/CLAUDE.md","territory":"dotclaude","target":"CLAUDE.md","strategy":"copy"},'
            '{"repo":"dotclaude/other.md","territory":"dotclaude","target":"other.md","strategy":"copy"}]}')


def run(cwd: Path, *args: str) -> None:
    r = subprocess.run([*GIT, "-C", str(cwd), *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed:\n{r.stderr}")


def write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8"))


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1]).resolve()
    mode = sys.argv[2] if len(sys.argv) > 2 else "default"
    if root.exists():
        sys.exit(f"refusing: {root} already exists")
    co, live, user = root / "checkout", root / "live", root / "user"
    for d in (co / "dotclaude", live, user):
        d.mkdir(parents=True)
    write(co / "ccs-manifest.json", MANIFEST)
    write(co / "dotclaude" / "other.md", "other v1\n")
    write(live / "other.md", "other v1\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(co)], capture_output=True)

    if mode == "--history":
        write(co / "dotclaude" / "CLAUDE.md", H_BASE)
        run(co, "add", "-A"); run(co, "commit", "-qm", "ancestor")
        write(co / "dotclaude" / "CLAUDE.md", H_THEIRS)
        run(co, "commit", "-qam", "upstream retires Legacy notes")
        write(live / "CLAUDE.md", H_OURS)
        write(root / "ancestor.md", H_BASE)
    elif mode == "--two-way":
        # the two unique lines must be DISSIMILAR (ratio < 0.5): near-twins
        # read as a rewrite of one another and the validator lets one go
        # (tester run-01, finding 1)
        write(co / "dotclaude" / "CLAUDE.md",
              "shared" + NL + "common" + NL + "- upstream added a paragraph about deploys" + NL)
        run(co, "add", "-A"); run(co, "commit", "-qm", "only commit")
        write(live / "CLAUDE.md", "shared" + NL + "common" + NL + "## Port 8443 on this box" + NL)
        write(root / "ancestor.md", "")
    else:
        write(co / "dotclaude" / "CLAUDE.md", SIBLING)
        run(co, "add", "-A"); run(co, "commit", "-qm", "post-fork payload revision")
        write(co / "dotclaude" / "CLAUDE.md", THEIRS)
        run(co, "commit", "-qam", "payload HEAD")
        write(live / "CLAUDE.md", OURS)
        write(root / "ancestor.md", BASE)
        home = root / "home-repo"
        write(home / ".claude" / "CLAUDE.md", BASE)
        subprocess.run(["git", "init", "-q", "-b", "main", str(home)], capture_output=True)
        run(home, "add", "-A"); run(home, "commit", "-qm", "the seeding machine's home repo")

    print(f"scratch world ({mode}) at {root}")
    print(f"  CCS prefix: ccs --checkout-dir {co} --claude-dir {live} --user-claude {user} --no-color --no-fetch")
    print(f"  ancestor:   {root / 'ancestor.md'}")
    if mode == "default":
        print(f"  home-repo:  {root / 'home-repo'}  (for --base-from {root / 'home-repo'}:.claude/CLAUDE.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
