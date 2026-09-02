"""Diagnostic: why does plan() exclude the kept seed in the constructed world
when the real payload planned settings.local.json? Dump every plan item and
the seed's state. Throwaway."""
import subprocess as sp, tempfile, json
from pathlib import Path
from dazzle_claude_config import merge, seeddecisions
from dazzle_claude_config.cli import _norm_sha, _seed_findings
from dazzle_claude_config.manifest import Manifest

GIT_ID = ["-c", "user.email=t@t.invalid", "-c", "user.name=t", "-c", "commit.gpgsign=false"]

tmp = Path(tempfile.mkdtemp(prefix="dbg-seed-"))
co = tmp / "checkout"; (co / "dotclaude").mkdir(parents=True)
live = tmp / "live"; live.mkdir()
uc = tmp / "userclaude"; uc.mkdir()

def git(*a):
    r = sp.run(["git", *GIT_ID, "-C", str(co), *a], capture_output=True, text=True)
    assert r.returncode == 0, f"git {a}: {r.stderr}"

sp.run(["git", "init", "-q", str(co)], capture_output=True)
(co / "ccs-manifest.json").write_text(
    '{"manifest_version":1,'
    '"territories":{"dotclaude":{"root_var":"CLAUDE_DIR","repo_dir":"dotclaude"}},'
    '"entries":['
    '{"repo":"dotclaude/F.md","territory":"dotclaude","target":"F.md","strategy":"copy"},'
    '{"repo":"dotclaude/SEED.md","territory":"dotclaude","target":"SEED.md",'
    '"strategy":"seed-if-absent"}]}', encoding="utf-8")
seed = b"starter\n"
(co / "dotclaude/F.md").write_bytes(b"shared\ncommon\n")
(co / "dotclaude/SEED.md").write_bytes(seed)
git("add", "-A"); git("commit", "-qm", "base")
(co / "dotclaude/F.md").write_bytes(b"shared\ncommon\nTHEIRS\n")
git("add", "-A"); git("commit", "-qm", "theirs")
(live / "F.md").write_bytes(b"shared\ncommon\nOURS\n")
(live / "SEED.md").write_bytes(b"starter\nmine, after delivery\n")
seeddecisions.keep("SEED.md", "until-changed", _norm_sha(seed), uc)

m = Manifest.load(co)
roots = {"CLAUDE_DIR": live, "USER_CLAUDE": uc}

print("=== manifest entries ===")
for e in m.entries:
    print(f"  repo={e.repo!r} target={e.target!r} territory={e.territory!r} strategy={e.strategy!r}")

print("=== _head_candidates ===")
for entry, rel, lv in merge._head_candidates(m, co, roots):
    print(f"  entry.repo={entry.repo!r} rel={rel!r} live={lv} exists={lv.is_file()}")

stage = tmp / "stage"
items = merge.plan(m, co, roots, stage=stage)
print("=== plan() items ===")
for i in items:
    print(f"  label={i.label!r} mergeable={i.mergeable} reason={i.reason!r} base={i.base}")

sf, err = _seed_findings(m, co, roots, None, frozenset(), uc)
print("=== _seed_findings ===", [(t, s) for t, s, *_ in sf], "errors=", err)

print("=== git show HEAD:dotclaude/SEED.md ===")
p = sp.run(["git", "-C", str(co), "show", "HEAD:dotclaude/SEED.md"], capture_output=True)
print(f"  rc={p.returncode} stdout={p.stdout!r}")
print(f"  live/SEED.md = {(live/'SEED.md').read_bytes()!r}")
