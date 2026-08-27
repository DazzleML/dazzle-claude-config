"""One-time extraction: KEYS' `explain` strings -> the packaged JSON.

Done mechanically rather than by retyping, because retyping this table has
already gone wrong once: transcribing `status_detail` by hand kept 'auto' and
silently dropped 'long' and 'compact'. The anti-drift test caught it, but the
cheaper answer is never to type it twice.

Run once, then the JSON is the source of truth and this script is history.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from dazzle_claude_config import userconfig  # noqa: E402

OUT = REPO / "dazzle_claude_config" / "settings-explanations.json"

body = {
    "_comment": (
        "What every ccs setting means, in plain language. This file ships "
        "inside the package, so `ccs setup update --explain <name>` answers "
        "offline with no config file and no network. Editing the prose here "
        "needs no Python. The setting's DEFAULT, environment variable and "
        "valid values are not here -- they live in dazzle_claude_config/"
        "userconfig.py, because they are behaviour rather than description, "
        "and a missing file must never mean ccs has no defaults."
    ),
    "_docs": "docs/configuration.md is generated from this file. Do not edit it by hand.",
    "settings": {name: k.explain for name, k in userconfig.KEYS.items()},
}

OUT.write_text(json.dumps(body, indent=2, ensure_ascii=True) + "\n",
               encoding="utf-8")

print(f"wrote {OUT}")
print(f"  settings: {len(body['settings'])}")
for name, text in body["settings"].items():
    print(f"  {name:<20} {len(text):>4} chars")
