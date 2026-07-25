# Changelog

All notable changes to dazzle-claude-config (ccs) are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow PEP 440.

## [Unreleased]

## [0.2.1] - 2026-07-25

### Added
- Common options accepted BOTH before and after the verb (human-test finding: everyone types `ccs status --checkout-dir X`)
- `CCS_CHECKOUT_DIR` environment variable, so a payload cloned outside user territory does not force `--checkout-dir` on every invocation (symmetric with `CLAUDE_CONFIG_DIR` for the config territory). Precedence: flag > env > default
- docs/platforms.md; ubuntu CI legs (full 3.10-3.13 matrix on Windows + Linux)
- PROJECT_PHASE=alpha display (`ccs ALPHA 0.2.1`); Development Status :: 3 - Alpha (pip version stays plain 0.2.1 so default pip resolves it)
- README: a "Where things live" section explaining the three locations (live config vs user territory vs payload checkout), a diagram of the collect/apply/git flow, how to turn an existing `~/.claude` into a payload repo, and that a checkout can hold your own config, someone else's, or a fork of theirs
- README: house-format Contributing / Related Projects / License sections

### Changed
- **Readable, colorized output** (human-test finding): `status` now reports all three legs of "in sync" -- what was compared (file/entry counts + the live directories), where the checkout sits relative to its remote (`on main, in sync with origin/main` instead of raw `## main...origin/main`) plus any uncommitted work in it, and a verdict that says what it means (`status: clean -- your live config and the checkout match; nothing to collect, nothing to apply`). Deny-list hits are now `protected ... matches a deny rule, so ccs will not copy it in either direction` rather than the opaque `note: denied, never syncs`. `diff` gained a legend. Color follows claude-session-backup's palette (bold cyan identifiers, green = nothing to do, yellow = attention, red = refusal), is emitted only to a TTY, and is disabled by `--no-color` or `NO_COLOR`
- **Deny-aware verbs (R6)**: deny-matched live files are the guard WORKING, not drift -- `status`/`diff` annotate ("denied, never syncs") and stay clean; `collect` notes them at exit 0. Only credential-SHAPED content in allowlisted files alarms (exit 1).

### Fixed
- **`.gitignore` was swallowing `dazzle_claude_config/secrets.py` and `tests/test_secrets.py`** (release blocker): the template's broad `*secret*` security pattern silently excluded this project's own guard-stack module and its tests, so the published repo could not be imported from a fresh clone (`ModuleNotFoundError: dazzle_claude_config.secrets`), CI failed on every push since v0.1.0, and a wheel built from that tree would have shipped broken. Explicit negations added. This is precisely the A8 failure class ccs exists to detect -- for payload repos; the tool's own repo had no such check
- **GitHub Pages stats dashboard showed "Unable to load statistics"**: `docs/stats/index.html` shipped with un-substituted template placeholders (`ARCHIVEID`, `OWNER`, `REPO`) and pointed its raw-data base at the ARCHIVE gist instead of the badge gist, so `state.json` 404'd. Both gist IDs, the owner/repo, and the creation date are now correct
- **`python -m dazzle_claude_config` always exited 0**: `__main__.py` discarded `main()`'s return value, silently voiding the A7 exit-code contract (0 clean / 1 drift / 2 error) for scripted and CI use of the module form. The `ccs` console script was unaffected. Found while documenting payload creation; the suite calls `main()` directly everywhere, which is why it went unnoticed -- the new regression test shells out to a real subprocess
- **Empty-repo guidance**: pointing ccs at the freshly-created empty repo you intend to *become* your payload previously produced a "does not look like a Claude config dir" error listing marker names. That is the make-a-payload case, not the wrong-repo case, so it now prints the four commands that turn an empty repo into a payload
- **`apply` now enforces the deny-list** (tester run-02, HIGH): the guard stack previously protected only collect's live->checkout direction, so a denied-named file committed in the payload (accidental commit, bad merge, or a raw `~/.claude` pushed via implicit mode) was copied straight into the live tree as an ordinary "applied:" line. Now both copy and seed paths refuse, report `REFUSED (deny-list ...)` with instructions to remove the file from the payload repo, and exit 1 -- a denied file IN the payload is an anomaly, unlike live-side denials (which remain exit-0 notes)
- **Implicit mode refuses wrong-typed markers** (tester run-02, MEDIUM): a repo whose only config marker matched by name but not type (e.g. a directory literally named `CLAUDE.md`) previously synthesized a zero-entry manifest and every verb reported a hollow "clean"/"nothing to do"; it now fails loudly (exit 2) like any non-config repo
- **Implicit mode ignores nested git repos**: `.git/` internals inside a mirror (e.g. a skill cloned straight into `skills/`) are excluded from sync in both directions, matching `__pycache__` handling. 74 tests.

## [0.2.0] - 2026-07-24

### Added
- **Layout-agnostic payloads**: a repo with no `ccs-manifest.json` that looks like a bare `~/.claude` mirror (root-level `CLAUDE.md`/`skills/`/`commands/`/`agents/`...) now works via an implicit manifest synthesized from the standard surfaces present -- anyone who pushed their config dir to GitHub as-is can point ccs at it. Non-config repos still fail with a clear error; the hard deny-list and secret scanning apply unchanged. (+5 tests, 58 total)

## [0.1.0] - 2026-07-24

### Added
- Phase 1 MVP: `ccs collect / apply / status / diff` against a manifest-driven payload checkout (`ccs-manifest.json`, strict v1 -- unknown keys are errors)
- Guard stack on collect: hard deny-list (credentials, `.claude.json`, `settings.local.json`, plugin state, databases) that overrides the manifest, plus credential-shape content scanning of ALL file types (`sk-ant-`, GitHub/AWS/Slack tokens, private-key headers) with reported -- never silent -- refusals
- Apply safety: timestamped pre-overwrite backups, staged (never in-place) removals behind `--sync-removals`, refusal while the checkout has unresolved merge conflicts, per-file failure reporting on locked/read-only destinations
- Home-repo isolation: structurally refuses to operate on a home-directory git repository or bind to a parent repo from a nested plain directory; no branch operations exist
- Git index verification after collect: files a machine-level ignore/exclude would silently drop are flagged (exit 2)
- Exit-code contract: 0 clean / 1 drift-or-refusals / 2 error; `--dry-run` on all mutating verbs; `--only` prefix filter with zero-match warning
- Console scripts `ccs` and `dazzle-claude-config`; stdlib-only, Python 3.10+
- 53 automated tests + tester-agent exploratory report + human test checklist (`tests/checklists/v0.1.0__Phase1__collect-apply-status-diff.md`)

[Unreleased]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/DazzleML/dazzle-claude-config/releases/tag/v0.1.0
