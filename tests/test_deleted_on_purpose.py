"""apply must not silently restore a file you deleted on purpose (#12).

Why this is a RECORD and not an inference: a path the checkout has and live
lacks reads two ways -- "you deleted it" and "it never reached this box" -- and
nothing inside the checkout can tell them apart. The issue proposed
`git log -- <path>`, but the checkout's history contains every checkout file
either way, so it answers "did the CHECKOUT have it", never "did LIVE have it".

So ccs asks. It reports what it installed into a tree that lacked it, and
honours a recorded answer. The record is the user's word, which is the one
form of this answer that cannot be wrong.
"""
from __future__ import annotations

import json
import subprocess as sp
from pathlib import Path

from dazzle_claude_config.cli import main

from conftest import GIT_ID

MANIFEST = {
    "manifest_version": 1,
    "territories": {"dotclaude": {"root_var": "CLAUDE_DIR", "repo_dir": "dotclaude"}},
    "entries": [{"repo": "dotclaude/skills", "territory": "dotclaude",
                 "target": "skills", "strategy": "copy"}],
}


def _git(cwd: Path, *args: str) -> None:
    r = sp.run(["git", *GIT_ID, "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"


def _world(tmp_path: Path) -> dict:
    co, live, user = tmp_path / "co", tmp_path / "live", tmp_path / "user"
    (co / "dotclaude" / "skills").mkdir(parents=True)
    (live / "skills").mkdir(parents=True)
    user.mkdir()
    (co / "ccs-manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    sp.run(["git", "init", "-q", "-b", "main", str(co)], check=True)
    (co / "dotclaude" / "skills" / "a.md").write_bytes(b"keep me\n")
    (co / "dotclaude" / "skills" / "unwanted.md").write_bytes(b"I removed this\n")
    _git(co, "add", "-A"); _git(co, "commit", "-qm", "v1")
    (co / "dotclaude" / "skills" / "a.md").write_bytes(b"keep me v2\n")
    _git(co, "commit", "-qam", "v2")
    # live has a.md (so it has plainly been applied here) but not unwanted.md
    (live / "skills" / "a.md").write_bytes(b"keep me v2\n")
    return dict(co=co, live=live, user=user)


def _ccs(w: dict, *verb: str) -> list[str]:
    return ["--checkout-dir", str(w["co"]), "--claude-dir", str(w["live"]),
            "--user-claude", str(w["user"]), "--no-color", "--no-fetch", *verb]


def _unwanted(w: dict) -> Path:
    return w["live"] / "skills" / "unwanted.md"


# -- the report ---------------------------------------------------------------

def test_apply_says_when_it_installs_a_file_live_did_not_have(tmp_path, capsys):
    """It still installs -- the defect was doing it SILENTLY."""
    w = _world(tmp_path)
    main(_ccs(w, "apply"))
    out = capsys.readouterr().out
    assert "not in your live config before this run" in out, out
    assert "--keep-deleted" in out, out
    assert _unwanted(w).exists()


def test_the_notice_NAMES_the_files_it_means(tmp_path, capsys):
    """A count is not actionable when the action takes a path.

    Found on a real run: "installed 5 files your live config did not have"
    printed ABOVE fourteen undifferentiated `applied:` lines, offering
    `--keep-deleted <path>` while naming no path. The reader had to
    cross-reference `ccs status` by hand to work out which five it meant.
    """
    w = _world(tmp_path)
    main(_ccs(w, "apply"))
    out = capsys.readouterr().out
    assert "skills/unwanted.md" in out
    notice = out.index("not in your live config before this run")
    assert out.index("applied:") < notice, \
        "the notice must come AFTER the list, or it reads as its heading"
    assert out.index("skills/unwanted.md", notice) > notice, \
        "the named file must appear under the notice, not only above it"


def test_an_ordinary_modification_does_not_trigger_the_notice(tmp_path, capsys):
    """The notice is for ABSENT files only; a normal update must stay quiet."""
    w = _world(tmp_path)
    main(_ccs(w, "apply"))                       # installs everything
    capsys.readouterr()
    (w["co"] / "dotclaude" / "skills" / "a.md").write_bytes(b"keep me v3\n")
    _git(w["co"], "commit", "-qam", "v3")
    main(_ccs(w, "apply"))
    out = capsys.readouterr().out
    assert "your live config did not have" not in out, out


# -- the record ---------------------------------------------------------------

def test_recording_a_deletion_stops_the_restore(tmp_path, capsys):
    w = _world(tmp_path)
    main(_ccs(w, "apply", "--keep-deleted", "skills/unwanted.md"))
    capsys.readouterr()
    main(_ccs(w, "apply"))
    out = capsys.readouterr().out
    assert not _unwanted(w).exists(), out
    assert "left out" in out and "on purpose" in out


def test_the_summary_does_not_claim_a_match_while_holding_a_file_out(tmp_path, capsys):
    """The same overclaim class as #29: something was held back, so say so."""
    w = _world(tmp_path)
    main(_ccs(w, "apply", "--keep-deleted", "skills/unwanted.md"))
    capsys.readouterr()
    main(_ccs(w, "apply"))
    out = capsys.readouterr().out
    assert "already matches the checkout" not in out, out


def test_restore_deleted_undoes_the_record(tmp_path, capsys):
    w = _world(tmp_path)
    main(_ccs(w, "apply", "--keep-deleted", "skills/unwanted.md"))
    main(_ccs(w, "apply"))
    assert not _unwanted(w).exists()
    capsys.readouterr()
    main(_ccs(w, "apply", "--restore-deleted", "skills/unwanted.md"))
    main(_ccs(w, "apply"))
    assert _unwanted(w).exists()


def test_the_record_lands_under_the_runs_user_claude_not_HOME(tmp_path, capsys):
    """The v0.5.5 backup-root bug, in a new file: pin the location."""
    w = _world(tmp_path)
    main(_ccs(w, "apply", "--keep-deleted", "skills/unwanted.md"))
    assert (w["user"] / "ccs-deleted.json").is_file()


def test_the_record_is_hand_editable_json(tmp_path, capsys):
    w = _world(tmp_path)
    main(_ccs(w, "apply", "--keep-deleted", "skills/unwanted.md"))
    data = json.loads((w["user"] / "ccs-deleted.json").read_text(encoding="utf-8"))
    assert "skills/unwanted.md" in data["deleted"]
    assert data["deleted"]["skills/unwanted.md"]["decision"] == "keep-deleted"
    assert "_comment" in data          # tells a human what the file is for


def test_a_malformed_record_warns_and_does_not_crash(tmp_path, capsys):
    w = _world(tmp_path)
    (w["user"] / "ccs-deleted.json").write_text("{ not json", encoding="utf-8")
    rc = main(_ccs(w, "apply"))
    out = capsys.readouterr().out
    assert "warning" in out.lower(), out
    assert _unwanted(w).exists(), "a broken record must not block the sync"
    assert rc in (0, 1)


def test_a_record_with_the_wrong_decision_field_is_rejected(tmp_path, capsys):
    """Validation must check the decision value, not merely that it is a dict.

    Caught by mutation J6: dropping the `decision == "keep-deleted"` check let
    ANY object in the file suppress a file. A record store that suppresses on
    malformed input widens what the tool withholds, which is the direction
    that loses the user data they expected to receive.
    """
    w = _world(tmp_path)
    (w["user"] / "ccs-deleted.json").write_text(
        json.dumps({"deleted": {"skills/unwanted.md": {"decision": "something-else"}}}),
        encoding="utf-8")
    main(_ccs(w, "apply"))
    out = capsys.readouterr().out
    assert "malformed" in out, out
    assert _unwanted(w).exists(), "a malformed record must not suppress the file"
