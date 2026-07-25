from dazzle_claude_config.secrets import SECRET_RE, is_denied, scan_file


def test_secret_shapes_match():
    fake = "x" * 24
    for token in (f"sk-ant-api03-{fake}", f"ghp_{fake}", f"github_pat_{fake}",
                  "AKIAABCDEFGHIJKLMNOP", f"xoxb-1234-{fake}",
                  "-----BEGIN RSA PRIVATE KEY-----"):
        assert SECRET_RE.search(f"key = {token}"), token


def test_benign_text_does_not_match():
    for line in ("the sk-ant- prefix is refused", "gh push", "AKIA is an AWS prefix",
                 "ghp_short"):
        assert not SECRET_RE.search(line), line


def test_hard_deny_matches_anywhere():
    assert is_denied(".credentials.json")
    assert is_denied("plugins/data/.credentials.json")
    assert is_denied("cache/session-backup.db")
    assert is_denied("settings.local.json")
    assert is_denied("history.jsonl")


def test_manifest_deny_extends_hard_deny():
    assert is_denied("notes.secret", ["*.secret"])
    assert not is_denied("notes.md", ["*.secret"])


def test_scan_file_reports_line(tmp_path):
    f = tmp_path / "cfg.md"
    f.write_text("line one\ntoken: sk-ant-api03-" + "y" * 20 + "\n", encoding="utf-8")
    hits = scan_file(f, "cfg.md")
    assert len(hits) == 1 and hits[0].line_no == 2
    assert hits[0].excerpt.startswith("sk-ant-")
