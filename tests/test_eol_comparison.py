"""files_differ must ignore line-ending style for text, but not for binary.

Motivating measurement, from a real payload on Windows:

    ccs status reported 67 modified files
    git diff  reported 20 with any content change
    47 were CRLF-vs-LF only

A repo with `* text=auto` stores LF and checks out CRLF on Windows, while the
live tree holds whatever wrote it. Byte comparison then reports permanent drift
on every Windows machine -- burying real edits, making collect rewrite dozens of
untouched files, and training the operator to skim the report that exists to be
read carefully before files move.
"""
from pathlib import Path

from dazzle_claude_config.syncmap import files_differ


def _w(p: Path, blob: bytes) -> Path:
    p.write_bytes(blob)
    return p


# --- the bug this fixes -------------------------------------------------

def test_crlf_vs_lf_is_not_drift(tmp_path):
    a = _w(tmp_path / "a.md", b"# Title\n\nbody line\n")
    b = _w(tmp_path / "b.md", b"# Title\r\n\r\nbody line\r\n")
    assert not files_differ(a, b)


def test_lone_cr_is_not_drift(tmp_path):
    """Classic-Mac CR endings normalise too."""
    a = _w(tmp_path / "a.md", b"one\ntwo\n")
    b = _w(tmp_path / "b.md", b"one\rtwo\r")
    assert not files_differ(a, b)


def test_mixed_endings_within_one_file(tmp_path):
    a = _w(tmp_path / "a.md", b"one\ntwo\nthree\n")
    b = _w(tmp_path / "b.md", b"one\r\ntwo\nthree\r\n")
    assert not files_differ(a, b)


# --- real differences must still be reported ----------------------------

def test_identical_bytes_are_not_drift(tmp_path):
    a = _w(tmp_path / "a.md", b"same\n")
    b = _w(tmp_path / "b.md", b"same\n")
    assert not files_differ(a, b)


def test_content_change_is_drift(tmp_path):
    a = _w(tmp_path / "a.md", b"alpha\n")
    b = _w(tmp_path / "b.md", b"beta\n")
    assert files_differ(a, b)


def test_content_change_masked_by_eol_change_is_still_drift(tmp_path):
    """The dangerous case: a real edit that also flips line endings."""
    a = _w(tmp_path / "a.md", b"alpha\nsecond\n")
    b = _w(tmp_path / "b.md", b"alpha\r\nCHANGED\r\n")
    assert files_differ(a, b)


def test_added_line_is_drift(tmp_path):
    a = _w(tmp_path / "a.md", b"one\ntwo\n")
    b = _w(tmp_path / "b.md", b"one\r\ntwo\r\nthree\r\n")
    assert files_differ(a, b)


def test_trailing_whitespace_is_drift(tmp_path):
    """Only line endings are normalised -- not other whitespace."""
    a = _w(tmp_path / "a.md", b"line\n")
    b = _w(tmp_path / "b.md", b"line   \n")
    assert files_differ(a, b)


# --- binary must never be normalised ------------------------------------

def test_binary_differing_only_by_cr_bytes_is_drift(tmp_path):
    """A 0x0D byte in binary is content, not a line ending.

    Without the NUL sniff these two would normalise to the same thing and a
    genuinely different file would be reported as identical -- silent data loss
    on the next sync.
    """
    a = _w(tmp_path / "a.png", b"\x89PNG\x00\x0d\x0a\x1a\x0a")
    b = _w(tmp_path / "b.png", b"\x89PNG\x00\x0a\x1a\x0a")
    assert files_differ(a, b)


def test_identical_binary_is_not_drift(tmp_path):
    blob = b"\x89PNG\x00\x0d\x0a\x1a\x0a\xff\xfe"
    a = _w(tmp_path / "a.png", blob)
    b = _w(tmp_path / "b.png", blob)
    assert not files_differ(a, b)


def test_binary_on_one_side_only_is_drift(tmp_path):
    a = _w(tmp_path / "a.bin", b"text\x00binary\n")
    b = _w(tmp_path / "b.bin", b"text\nbinary\n")
    assert files_differ(a, b)


# --- failure modes ------------------------------------------------------

def test_unreadable_file_reports_drift(tmp_path):
    """Unreadable must never be mistaken for "same" -- fail loud, not silent."""
    a = _w(tmp_path / "a.md", b"content\n")
    missing = tmp_path / "gone.md"
    assert files_differ(a, missing)


def test_empty_files_are_not_drift(tmp_path):
    a = _w(tmp_path / "a.md", b"")
    b = _w(tmp_path / "b.md", b"")
    assert not files_differ(a, b)


def test_empty_vs_nonempty_is_drift(tmp_path):
    a = _w(tmp_path / "a.md", b"")
    b = _w(tmp_path / "b.md", b"x\n")
    assert files_differ(a, b)
