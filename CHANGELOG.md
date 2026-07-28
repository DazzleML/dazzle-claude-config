# Changelog

All notable changes to dazzle-claude-config (ccs) are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow PEP 440.

## [Unreleased]

## [0.3.0] - 2026-07-28

### Added
- **`ccs merge` -- reconcile files that changed on BOTH sides.** Until now ccs could copy live-to-checkout (`collect`) or checkout-to-live (`apply`), and nothing else. Both are one-way, so a file edited on two machines lost one side's work whichever verb you ran. `merge` assembles the two versions plus a common ancestor and hands them to your own diff tool through git's `mergetool` contract, so any of the ~20 tools git already knows -- Beyond Compare, vimdiff, WinMerge, Meld, KDiff3, VS Code -- works with no configuration from ccs.

  Nothing is installed until the result passes validation. `--preview` opens the three versions to look at without producing or installing anything; `--union` keeps both sides where they added different things; `--accept` installs, backing up both originals first.

- **Two-way divergence is now refused by `collect` and `apply`.** They previously treated a file that differed on both sides exactly like a one-sided change and copied straight over it, reporting success. Both now stop, name the files, and point at `ccs merge`; `--force` still overwrites for anyone who genuinely wants that.

- **`ccs status` reports which side owns each change.** Instead of "1 differs on both sides" -- a file count that reads like a difference count -- entries now show `2 lines only in live, 6 changed on both, 50 lines only in the checkout, in 10 regions`, with a per-file breakdown indented under each entry. Verbosity is `auto` by default, collapsing past a line budget; `--long`/`--compact`, `CCS_STATUS_DETAIL`/`CCS_STATUS_MAX_LINES`, and `~/claude/ccs-config.json` all control it.

- **User preferences file** at `~/claude/ccs-config.json`, covering diff tool, status verbosity, divergence policy, and the AI-merge hook. Precedence is flag, then environment variable, then file, then built-in.

### Changed
- `ccs status` labels the live roots the way it already labelled the checkout, and its footer leads with `ccs merge` when any entry differs on both sides -- previously it recommended `collect` or `apply` there, which is precisely the advice that discards work.
- `--help` now documents the day-to-day loop, not only first-time installation.

### Fixed
- **Line endings no longer wreck the merge.** The live tree is CRLF on Windows while git returns LF, so an unnormalised comparison marked every line as changed and collapsed the merge into one ~1000-line conflict. Normalising first took a real 1014-vs-966-line divergence from 1983 lines of conflict to 65, with 93% auto-resolved. Write-back restores the file's original line-ending style.
- **A merge base is no longer trusted just because it is the nearest commit.** When the closest historical version is a *sibling* rather than an ancestor -- it contains content one side never had -- a three-way merge reads "never had it" as "deleted it" and removes it. Measured: a coherent 10-line block silently dropped, leaving the surrounding section incoherent. ccs now rejects a candidate base when the lines it attributes to your deletions are still present verbatim on the other side, falling back to an honest two-way. `--base sibling` opts back in; the rejected version is written to the workspace either way, clearly named, so it can still be inspected.

### Removed
- `difftool.py`, an unused diff-tool registry. `git mergetool` already resolves and launches these tools, including version-suffixed entries.

### Known issues
- **`ccs merge --accept` only installs one side.** The merged result reaches the live tree correctly, but the checkout copy is written to the staging file in the merge workspace instead of the checkout's real path, so the payload repo never receives it -- and the staged "theirs" copy is overwritten in the process. `ccs status` will still report the file as divergent afterwards, which is the honest signal that the install was partial.
- **The pre-install backup is not written.** `--accept` prints `originals backed up: ~/claude/backups/ccs-merge` but the directory is never created, for the same reason. Until this is fixed, take your own copy of both sides before using `--accept`.

  Both were found by verifying a real install rather than by the test suite, which asserted `_write_back` against synthetic paths where the distinction does not exist. Fixed in 0.3.1.

### Notes
- Ctrl+C handling and the non-blocking `--preview` are **not yet verified**: both involve console signals combined with a GUI process and cannot be exercised from a non-interactive shell.
- `ai_merge_command` is present in the config file but **not yet implemented**; `ccs merge` currently suggests it and prints deterministic resolution hints (regressed patterns, convention drift) instead.

## [0.2.3] - 2026-07-27

### Fixed
- **Line-ending style is no longer reported as drift.** If your payload has `* text=auto` in `.gitattributes` -- the usual setting -- git stores LF and checks out CRLF on Windows, while your live `~/.claude` holds whatever wrote it, usually LF. ccs compared raw bytes, so every text file differed on every Windows machine, permanently. Measured against a real payload:

  ```
  ccs status   67 files modified
  git diff     20 files with any content change
               47 were line endings only
  ```

  ccs now reports 20, matching git exactly. Comparison normalises line endings for text and compares bytes for binary -- the same rule git applies.

  This mattered more than it looks. The noise buried 20 genuine edits among 67 reported ones, made `collect` rewrite 47 untouched files, and turned the report you are meant to read carefully *before* files move into something you skim. Working out which 20 mattered took a purpose-written triage script, which is the drift report failing at its only job.

  Comparison only: nothing here rewrites a file. Canonicalising line endings during `apply` would mean editing your live config to fix a reporting problem.

- **Binary files are never normalised.** A `0x0D` byte inside a PNG is content, not a line ending. Files are sniffed for a NUL byte in the first 8000 bytes -- the same heuristic git uses -- and compared byte-for-byte when binary. Without this, two genuinely different binaries could compare equal and a real change would be dropped on the next sync.

- **Comparing against a missing or unreadable file now reports drift instead of raising.** `filecmp.cmp` throws before the file is read, so the guard has to cover both paths. Callers check existence first today, but a comparison helper that raises on a missing path is a trap for the next caller.

### Tests
- 14 cases: CRLF/CR/mixed normalisation, real content changes still detected (including an edit that *also* flips line endings), trailing whitespace still significant, binary never normalised, missing files, empty files.

## [0.2.2] - 2026-07-25

### Fixed
- **Pointing `--checkout-dir` at a folder inside another git repository is now refused instead of silently accepted.** If your payload checkout was a plain folder living inside some *other* repo (a monorepo, a scratch tree, anything with a `.git` above it), ccs would proceed as though it were an ordinary non-git folder -- quietly skipping two safety checks in the process: the verification that collected files actually reach git's index (so a stray ignore rule can't silently drop config from your payload), and the refusal to apply while the checkout has unresolved merge conflicts. Neither omission was reported. ccs now stops with exit 2 and tells you how to resolve it:

  ```
  ccs: not a git repository root: /path/to/checkout (inside repo /path/to/outer)
       -- ccs will not bind to a parent repository; move the checkout outside it, or `git init` it
  ```

  Checkouts that are their own git repository, and plain folders not inside any repository, are unaffected.
- **Contributors:** one test asserted a specific one of two equally-correct refusals, so it passed or failed depending on whether the machine's temp directory happened to sit inside a git repository, rather than on ccs's behavior. It now accepts either.

## [0.2.1] - 2026-07-25

### Added
- Common options accepted BOTH before and after the verb (human-test finding: everyone types `ccs status --checkout-dir X`)
- `CCS_CHECKOUT_DIR` environment variable, so a payload cloned outside user territory does not force `--checkout-dir` on every invocation (symmetric with `CLAUDE_CONFIG_DIR` for the config territory). Precedence: flag > env > default
- `docs/platforms.md` describing tested vs expected platforms; Linux added to CI (Windows + Ubuntu, Python 3.10-3.13)
- Alpha designation shown in `ccs --version` (`ccs ALPHA 0.2.1`) and in the PyPI classifier. The installable version stays plain `0.2.1` -- a PEP 440 pre-release suffix would make `pip install` skip it by default
- README: a "Where things live" section explaining the three locations (live config vs user territory vs payload checkout), a diagram of the collect/apply/git flow, how to turn an existing `~/.claude` into a payload repo, and that a checkout can hold your own config, someone else's, or a fork of theirs
- README: house-format Contributing / Related Projects / License sections

### Changed
- **Readable, colorized output** (human-test finding): `status` now reports all three legs of "in sync" -- what was compared (file/entry counts + the live directories), where the checkout sits relative to its remote (`on main, in sync with origin/main` instead of raw `## main...origin/main`) plus any uncommitted work in it, and a verdict that says what it means (`status: clean -- your live config and the checkout match; nothing to collect, nothing to apply`). Deny-list hits are now `protected ... matches a deny rule, so ccs will not copy it in either direction` rather than the opaque `note: denied, never syncs`. `diff` gained a legend. Color follows claude-session-backup's palette (bold cyan identifiers, green = nothing to do, yellow = attention, red = refusal), is emitted only to a TTY, and is disabled by `--no-color` or `NO_COLOR`
- **Protected files no longer read as a problem.** A file that matches a deny rule can never sync, so its presence is the intended state, not drift: `status` and `diff` annotate it and still report clean, and `collect` notes it and exits 0. Only credential-shaped *content* found inside a file that ccs was otherwise willing to copy raises an alarm (exit 1). Previously every run reported perpetual drift for files that were being protected exactly as designed.

### Fixed
- **`.gitignore` was swallowing `dazzle_claude_config/secrets.py` and `tests/test_secrets.py`** (release blocker): the template's broad `*secret*` security pattern silently excluded this project's own guard-stack module and its tests, so the published repo could not be imported from a fresh clone (`ModuleNotFoundError: dazzle_claude_config.secrets`), CI failed on every push since v0.1.0, and a wheel built from that tree would have shipped broken. Explicit un-ignore rules added. This is exactly the failure ccs already detects for *payload* repos -- an ignore rule silently dropping files that were supposed to be tracked -- but nothing performed that check on ccs's own repository
- **GitHub Pages stats dashboard showed "Unable to load statistics"**: `docs/stats/index.html` shipped with un-substituted template placeholders (`ARCHIVEID`, `OWNER`, `REPO`) and pointed its raw-data base at the ARCHIVE gist instead of the badge gist, so `state.json` 404'd. Both gist IDs, the owner/repo, and the creation date are now correct
- **`python -m dazzle_claude_config` always exited 0**: `__main__.py` discarded the return value, so the documented exit codes (0 clean / 1 drift / 2 error) were lost for anyone scripting the module form -- a CI job polling for drift would have seen success forever. The `ccs` console script was unaffected. Found while documenting payload creation; the suite calls `main()` directly everywhere, which is why it went unnoticed -- the new regression test shells out to a real subprocess
- **Empty-repo guidance**: pointing ccs at the freshly-created empty repo you intend to *become* your payload previously produced a "does not look like a Claude config dir" error listing marker names. That is the make-a-payload case, not the wrong-repo case, so it now prints the four commands that turn an empty repo into a payload
- **`apply` now enforces the deny-list too** (found by an adversarial test run; report in `tests/checklists/results/`): deny rules previously guarded only the live-to-checkout direction, so a denied-named file committed in the payload (accidental commit, bad merge, or a raw `~/.claude` pushed via implicit mode) was copied straight into the live tree as an ordinary "applied:" line. Now both copy and seed paths refuse, report `REFUSED (deny-list ...)` with instructions to remove the file from the payload repo, and exit 1 -- a denied file IN the payload is an anomaly, unlike live-side denials (which remain exit-0 notes)
- **A repo whose config marker is the wrong kind of thing is now refused**: a repo whose only config marker matched by name but not type (e.g. a directory literally named `CLAUDE.md`) previously synthesized a zero-entry manifest and every verb reported a hollow "clean"/"nothing to do"; it now fails loudly (exit 2) like any non-config repo
- **Implicit mode ignores nested git repos**: `.git/` internals inside a mirror (e.g. a skill cloned straight into `skills/`) are excluded from sync in both directions, matching `__pycache__` handling. 74 tests.

## [0.2.0] - 2026-07-24

### Added
- **Layout-agnostic payloads**: a repo with no `ccs-manifest.json` that looks like a bare `~/.claude` mirror (root-level `CLAUDE.md`/`skills/`/`commands/`/`agents/`...) now works via an implicit manifest synthesized from the standard surfaces present -- anyone who pushed their config dir to GitHub as-is can point ccs at it. Non-config repos still fail with a clear error; the hard deny-list and secret scanning apply unchanged. (+5 tests, 58 total)

## [0.1.0] - 2026-07-24

### Added
- First working version: `ccs collect / apply / status / diff` against a manifest-driven payload checkout (`ccs-manifest.json`, strict v1 -- unknown keys are errors)
- Guard stack on collect: hard deny-list (credentials, `.claude.json`, `settings.local.json`, plugin state, databases) that overrides the manifest, plus credential-shape content scanning of ALL file types (`sk-ant-`, GitHub/AWS/Slack tokens, private-key headers) with reported -- never silent -- refusals
- Apply safety: timestamped pre-overwrite backups, staged (never in-place) removals behind `--sync-removals`, refusal while the checkout has unresolved merge conflicts, per-file failure reporting on locked/read-only destinations
- Home-repo isolation: structurally refuses to operate on a home-directory git repository or bind to a parent repo from a nested plain directory; no branch operations exist
- Git index verification after collect: files a machine-level ignore/exclude would silently drop are flagged (exit 2)
- Exit-code contract: 0 clean / 1 drift-or-refusals / 2 error; `--dry-run` on all mutating verbs; `--only` prefix filter with zero-match warning
- Console scripts `ccs` and `dazzle-claude-config`; stdlib-only, Python 3.10+
- 53 automated tests + tester-agent exploratory report + human test checklist (`tests/checklists/v0.1.0__Phase1__collect-apply-status-diff.md`)

[Unreleased]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.3.0...HEAD
[0.2.3]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/DazzleML/dazzle-claude-config/releases/tag/v0.1.0
