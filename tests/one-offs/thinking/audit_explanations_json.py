"""Red-green audit for the explanations-as-packaged-data change.

Each entry neutralises ONE production behaviour and runs the test that claims
to prove it. A test that still passes is not an anchor -- it is an invariant
guard, which is worth having but must not be counted as proof.

The audit verifies the neutralisation TOOK before believing the result: a sed
pattern that silently matched nothing produces a no-op audit, which looks
exactly like a weak test set. That happened once this project and reported a
false "1 of 8".
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

CLI = REPO / "dazzle_claude_config" / "cli.py"
UC = REPO / "dazzle_claude_config" / "userconfig.py"
PYPROJ = REPO / "pyproject.toml"
JSON = REPO / "dazzle_claude_config" / "settings-explanations.json"

CASES = [
    ("console gets the index, not 80 lines", CLI,
     "if not merge._console_attached(sys.stdout):",
     "if True:  # NEUTRALISED",
     "tests/test_setup_update.py::"
     "test_at_a_console_the_bare_form_is_an_INDEX_not_an_80_line_dump"),

    ("redirected output is never prompted", CLI,
     "if not merge._console_attached(sys.stdout):",
     "if False:  # NEUTRALISED",
     "tests/test_setup_update.py::"
     "test_redirected_output_gets_EVERYTHING_and_is_never_prompted"),

    ("a console that cannot be asked is not asked", CLI,
     "    if not merge._console_attached(sys.stdin):\n"
     "        return EXIT_CLEAN  # can show, cannot ask\n",
     "",
     "tests/test_setup_update.py::"
     "test_a_console_that_cannot_be_ASKED_shows_the_index_and_stops"),

    ("CCS_INTERACTIVE=0 suppresses the prompt", CLI,
     'if os.environ.get("CCS_INTERACTIVE", "").strip().lower() in \\',
     'if False and os.environ.get("CCS_INTERACTIVE", "").lower() in \\',
     "tests/test_setup_update.py::test_CCS_INTERACTIVE_off_suppresses_the_prompt"),

    ("truncation is ASCII, printable on codepage 437", CLI,
     'return cut + "..."',
     'return cut + "…"',
     "tests/test_setup_update.py::"
     "test_the_index_uses_no_characters_cmd_exe_cannot_print"),

    ("an unexplained setting is DETECTED", UC,
     "    named = set(keys)",
     "    named = set()  # NEUTRALISED",
     "tests/test_config_keys_table.py::"
     "test_an_unexplained_setting_is_DETECTED_not_merely_absent_today"),

    ("a whitespace-only explanation counts as missing", UC,
     '{n for n, t in texts.items() if (t or "").strip()}',
     "{n for n, t in texts.items() if t is not None}",
     "tests/test_config_keys_table.py::"
     "test_a_whitespace_only_explanation_counts_as_missing"),

    ("pyproject ships the data file", PYPROJ,
     "[tool.setuptools.package-data]",
     "[tool.setuptools.package-data-NEUTRALISED]",
     "tests/test_explanations_ship.py::"
     "test_pyproject_declares_the_file_as_package_data"),

    ("the docs page cannot fall behind the words", JSON,
     "What to do when a file has diverged two ways.",
     "NEUTRALISED: edited without regenerating the page.",
     "tests/test_explanations_ship.py::"
     "test_the_docs_page_matches_the_settings_it_documents"),

    ("a malformed file degrades instead of raising", UC,
     "    except ValueError as exc:\n"
     "        return {}, f\"{EXPLANATIONS_FILE} is not valid JSON ({exc})\"",
     "    except ValueError:\n        raise",
     "tests/test_explanations_ship.py::"
     "test_a_malformed_file_degrades_and_says_why"),
]


def run(nodeid: str) -> bool:
    r = subprocess.run([sys.executable, "-m", "pytest", nodeid, "-q",
                        "--no-header", "-p", "no:cacheprovider"],
                       cwd=str(REPO), capture_output=True, text=True)
    return r.returncode == 0


def main() -> int:
    anchors, guards, broken = [], [], []
    for label, path, find, replace, nodeid in CASES:
        backup = path.with_suffix(path.suffix + ".audit-backup")
        shutil.copy2(path, backup)
        try:
            original = path.read_text(encoding="utf-8")
            if find not in original:
                broken.append((label, "PATTERN DID NOT MATCH -- audit is a "
                                      "no-op, result would be meaningless"))
                continue
            path.write_text(original.replace(find, replace, 1),
                            encoding="utf-8")
            still_passes = run(nodeid)
            (guards if still_passes else anchors).append(label)
        finally:
            shutil.copy2(backup, path)
            backup.unlink()

    print(f"\nANCHORS  ({len(anchors)}) -- fail without the fix, real proof")
    for a in anchors:
        print(f"  [anchor] {a}")
    print(f"\nGUARDS   ({len(guards)}) -- pass either way, worth having, "
          f"NOT proof")
    for g in guards:
        print(f"  [guard]  {g}")
    if broken:
        print(f"\nBROKEN AUDITS ({len(broken)}) -- fix these before believing "
              f"anything above")
        for b, why in broken:
            print(f"  [BROKEN] {b}: {why}")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
