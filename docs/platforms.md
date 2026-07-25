# Platform support

ccs is stdlib-only Python 3.10+ with git as its sole external dependency — deliberately no platform-specific code paths.

| Platform | Status | Notes |
|---|---|---|
| Windows 10/11 | **Tested** (primary dev) | Full pytest matrix (3.10–3.13) in CI; daily-driven against a real config; console scripts via pip |
| Linux | **CI-tested** | ubuntu-latest legs in CI (3.10–3.13); human checklist run pending |
| macOS | Expected | No known platform code; POSIX paths native |
| BSD | Expected | Assumes only python3 + git |

"Tested" = exercised by CI and/or daily maintainer use. "Expected" = no known platform-specific behavior, not yet regularly exercised — reports welcome on [Quick Notes](https://github.com/DazzleML/dazzle-claude-config/issues/5).

Deliberate choices: `pathlib` throughout; `CLAUDE_CONFIG_DIR` honored for relocated config dirs; backups and staged removals use plain file copies (no symlinks); git operations shell out to your git (no bundled libgit); the home-repo safety guard works identically everywhere.
