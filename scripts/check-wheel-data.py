"""Assert the non-Python files ccs needs are actually inside the built wheel.

A test can prove a data file exists in the source tree. Only a built wheel can
prove it reaches a machine somebody installed the tool on -- and that is the
only place the failure ever shows. `[tool.setuptools.packages.find]` collects
modules; a `.json` beside them ships only because `[tool.setuptools.package-
data]` says so, and if that line is missing or misspelt then `ccs setup update
--explain` works in every checkout and answers with nothing after
`pip install`. Invisible where the work is done, visible only on the VPS.

    python scripts/check-wheel-data.py            # checks dist/*.whl
    python scripts/check-wheel-data.py dist/x.whl

Exit 0 if every required file is present, 1 if any is missing.
"""
from __future__ import annotations

import glob
import sys
import zipfile

#: Files that must be in the wheel, and what breaks on the installed machine
#: if they are not. Add to this whenever a non-.py file becomes load-bearing.
REQUIRED = {
    "dazzle_claude_config/settings-explanations.json":
        "`ccs setup update --explain` would print nothing",
    "dazzle_claude_config/merge-tools.json":
        "every merge tool would fall to the built-in capability table and no "
        "injection profile would exist -- `ccs merge --relaunch` could never "
        "restore work into BeyondCompare",
    "dazzle_claude_config/inject.ps1":
        "the injection driver would be absent: `ccs merge --relaunch` on an "
        "inject-capable tool would fall to the floor (file left closed) on "
        "every installed Windows box",
}


def main(argv: list[str]) -> int:
    wheels = argv[1:] or sorted(glob.glob("dist/*.whl"))
    if not wheels:
        print("no wheel found -- run `python -m build` first", file=sys.stderr)
        return 1

    bad = False
    for wheel in wheels:
        names = set(zipfile.ZipFile(wheel).namelist())
        for path, consequence in REQUIRED.items():
            if path in names:
                print(f"ok: {wheel} ships {path}")
                continue
            bad = True
            print(f"MISSING from {wheel}: {path}", file=sys.stderr)
            print(f"  consequence: {consequence}", file=sys.stderr)
            print("  fix: add it under [tool.setuptools.package-data] in "
                  "pyproject.toml", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
