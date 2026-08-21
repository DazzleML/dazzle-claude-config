# Changelog

All notable changes to dazzle-claude-config (ccs) are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow PEP 440.

## [Unreleased]

## [0.4.1] - 2026-08-21

The base problem, finished as far as history can finish it. `ccs` has to guess which commit a live tree was last synced to -- nothing records it -- and every verdict about "who changed this file" rests on that guess. This release fixes three ways the guess was wrong, makes `status` show its evidence, and stops the one-way verbs from copying in the wrong direction. Measured on a real month-old drift: 21 files the tool called "changed on both sides" were untouched on the live side; 3 it called two-sided were untouched on the checkout side; zero actually needed a merge.

### Fixed
- **One-sided drift is no longer reported as two-sided.** The base finder threw away any historical version equal to either side, so a live file that simply had not changed since an older commit -- or a checkout that had not -- was treated as if both had moved. That sent users into empty merges and, once the directory-entry guard landed, into per-file refusals. An equal ancestor is now the strongest evidence there is, and it is used.
- **An ordinary edit after syncing is no longer refused.** The finder also excluded the checkout's latest commit from consideration -- but that commit *is* the sync point in the most common workflow, apply-then-edit. Excluding it meant every post-sync edit of a single-file entry was refused as two-sided; it is why every refusal anyone saw was `CLAUDE.md`. The latest commit is now a candidate, with two safeguards: it must beat an older version *strictly* (a tie went to it by an immunity it had not earned), and it is never the only candidate (with a one-commit history the situation is indistinguishable from a live tree that never synced from here, and the tool refuses rather than guesses).
- **A rejected base no longer falls back to a farther one.** When the nearest historical version is ruled out as a false ancestor, the walk used to settle for the next one -- or the latest commit -- which flipped a correct refusal into a silent pass. It now refuses. Found by exhaustively enumerating the classifier's state space (`tests/one-offs/enumerate_classifier_states.py`) and confirmed by a two-machine simulator (`tests/one-offs/sim_two_machines.py`); both ride in the repo.
- **`ccs diff <path>` prints again.** The textual form had been calling a function that did not exist (only the `--difftool` form survived); it now prints a unified diff, says `identical` when the two sides agree, and exits 2 for an unknown path. It also survives a Windows console that cannot encode emoji -- config files carry them, and a read-only verb must not crash on the user's own file.
- **`status` no longer says "both sides" for files that merely differ.** The per-file label came from a two-way comparison with no base consulted, so "differs on both sides" meant "differs." Each differing file is now attributed through the same base inference the collect/apply guard uses -- `status` and the guard cannot disagree -- and `--long` shows the evidence: `checkout ahead; live == db89bcf`, `live ahead; checkout == 40aa09a`, `both moved since <sha>`, `never committed -- local snapshot`. The "changed on both" line count is now "replaced", which is what a two-way diff actually measures.

- **`ccs diff <bare filename>` no longer guesses when two entries hold a file of that name.** `ccs diff SKILL.md` with a `SKILL.md` under two different entries silently opened the first one in manifest order. It now refuses, lists the candidates, and asks for a qualified path; the match is also on whole path components, so `SAME.md` never lands on `notSAME.md`. Found by the release's own checklist sweep.

### Added
- **`ccs diff <path> --difftool 3`: the three-way view.** `--difftool` (or `--difftool 2`) opens live against the checkout as before; `3` opens live, the commit `status` named as the common ancestor, and the checkout in your merge tool, read-only -- the output pane is a scratch copy and nothing is written back. It is how the attribution gets checked by eye: on a "checkout ahead" file the middle pane equals the left, on "live ahead" it equals the right, on a two-sided file neither. `merge --preview` only opens files that differ on both sides; one-sided files, where the attribution matters just as much, had no three-pane view at all. When no ancestor can be found it says why and opens the two-way view instead.

### Changed
- **`apply` and `collect` are direction-aware.** A one-sided file is safe for one verb, not both: live-ahead has nothing to apply (applying would revert your edits), checkout-ahead has nothing to collect (collecting would undo the other machine's work). Each verb now skips the wrong direction and says why, using the attribution above. Before this, running both verbs to "sync everything" silently clobbered one set each way -- the two-way guard, correctly, never fired.

### Known limits, stated
- Without a recorded sync point, two situations remain undecidable from the files and history alone: a live tree that never synced from this checkout (adoption), and a live file hand-reverted byte-for-byte to an old commit. Both are refused or pass conservatively; neither is guessed. Recording the sync point is the next piece of work.

### Fixed (from 0.4.1a0, interim)
- **The two-way refusal now protects files inside directory entries** -- which is nearly every file in a real payload. Since it shipped, the guard checked whether each manifest entry's target was a single file and silently skipped every directory-shaped entry (`skills/`, `commands/`), so only single-file entries like a top-level `CLAUDE.md` were ever protected: a `collect` or `apply` could overwrite a genuinely diverged file inside `skills/` and report success. Found by a checklist step written to test message ordering; reproduced on a real payload. Refusals now name the individual files at risk.
- **Merges of files inside directory entries now get a real ancestor.** The same skip lived in merge's history-aware path, so those files always merged as baseless two-ways even when a perfectly good common ancestor sat in the checkout's history.
- **A diverged seed file no longer blocks the whole run.** Seed-if-absent entries can't be damaged by either one-way verb -- `collect` never touches them and `apply` never overwrites an existing live file for them -- so refusing everything over one was pure over-refusal, and on a real payload it masked every other report in the run. `ccs merge` still offers them.

### Known, temporary (from 0.4.1a0 -- resolved above)
- 0.4.1a0 shipped with a stated gap: files whose live copy simply *hadn't changed* since an older commit were refused as if both sides had moved -- loudly, per-file, with `ccs merge` suggested. Loud false refusal replaced silent data loss on purpose, for the few hours between the two halves. The base-inference fixes at the top of this entry dissolve it.

## [0.4.0] - 2026-07-31

Selective sync. `collect` could copy a whole payload or nothing, which is fine for a private repo you sync with yourself and wrong for one you publish. Measured on a real public collection: an ordinary `ccs collect` would have copied thirteen personal files -- task-manager commands and two machine-specific agents -- into a repo bound for GitHub, and the only thing that stopped it was an unrelated refusal about a different file.

### Added
- **`ccs collect --only <prefix>`**, matching `apply` and `merge`, which had it already. Limits a collect to entries whose repo path starts with the prefix, so you can send one part of your config without sending the rest. A prefix matching nothing now says so, instead of reporting a successful copy of zero files.
- **`hold_additions` in `ccs-manifest.json`.** When set, `collect` updates files the checkout already tracks but will not create new ones without `--add`, and names each file it held back along with how to include it or exclude it for good. Meant for a payload pushed somewhere public or shared, where an unintended new file is *published* rather than merely copied. Defaults to off, so existing checkouts behave exactly as before.
- **`ccs collect --add`**, the escape hatch for the above.
- **Editor and OS clutter excluded by default** -- `.vscode/`, `.idea/`, `.DS_Store` join `__pycache__` and `*.pyc`. Found by running a real collect, which turned up a `.vscode/settings.json` nested inside a skill directory and headed for a public repo.

### Notes
- The first `collect` against an entry the checkout carries nothing for is treated as adoption, and its files are added even under `hold_additions` -- refusing them would make the first run a silent no-op.
- Exclusion patterns match paths relative to an entry's *target*, and also a bare filename or any directory segment.
- Publication safety is a property of the payload, not of the verb: the private repo you sync with yourself wants new files picked up silently, and the public one does not. That is why the setting lives in the manifest rather than being a global default.

## [0.3.1] - 2026-07-28

Fixes both issues 0.3.0 shipped as known, plus five more found by running the tool against a real config rather than a fixture. Every one was the same defect wearing a different costume -- a comparison made against the wrong reference -- which is written up in the reference-model analysis noted at the end.

### Fixed
- **`ccs merge --accept` now installs into the checkout, not into its own scratch directory.** `MergeItem.repo` was serving two jobs at once: *the content of theirs* and *where the result gets installed*. On the incoming-upstream axis those are different paths, so the merge landed in the live tree and in a workspace staging file, leaving the payload repo untouched while reporting success. Destination is now a separate field.
- **The pre-install backup is actually written**, and written *before* either side is touched. It backs up the checkout's real file rather than the staging copy, so `~/claude/backups/ccs-merge/` now genuinely contains both originals.
- **Re-running `merge` no longer discards your edits.** The resume check compared the output pane against *ours*; it now compares against what ccs seeded, recorded in a sidecar. The old test was wrong in both directions -- a union seed never equals ours, so every re-run claimed "resumed" even when nothing had been touched, and a resolution that happened to match ours was mistaken for an untouched seed and overwritten.
- **A merge whose result equals what you already have is accepted.** It asked what the result *resembled* rather than what was *lost*, so a file where the other side had nothing unique to contribute was rejected as "the other side contributed nothing" -- true, and not a problem. Loss is now judged per side from lines dropped outright.
- **Superseded wording is no longer counted as lost content.** "Two of these are re-entrant" against "Three of these are re-entrant" is a rewrite, not a deletion; keeping both would make the document contradict itself. A replacement similar to what it replaced is a rewrite, a dissimilar one is a clobber, and only clobbers are loss. Dropping a known regressed pattern is likewise not loss -- the tool recommends dropping it.
- **`collect` and `apply` no longer refuse a merge you already finished.** The two-way guard compared live against **HEAD**, but a merge installs into the working tree and HEAD does not move until you commit -- so a completed merge was reported as unresolved indefinitely. Resolution is judged from the working tree.
- **A file that exists only in your live config is no longer reported as a pending removal.** `apply` treated "absent from the checkout" as "the checkout deleted it", implying you had to run `collect` first over a file that was never at risk. Git is now asked whether the path ever existed; one that never did is reported as `local only -- new here, never in the checkout; left alone`.
- **Ctrl+C interrupts `ccs merge` immediately** (verified). `Popen.wait()` blocks in `WaitForSingleObject` on Windows, so the interrupt was queued and only delivered once the diff tool was closed -- exactly when it was no longer wanted. The wait now polls. The tool is left open and untouched; an earlier attempt killed the whole process tree, which closed the user's editor.
- **`ccs merge --preview` returns immediately** (verified) instead of blocking the shell until the diff tool is closed. There is nothing to come back for in a preview.

### Added
- **`ccs diff <path>`** -- the line-by-line difference for one file, live vs checkout, rather than only a list of which files differ. Added because "merged and installed" is a claim with no way to check it. Distinguishes three cases that were previously conflated: the file differs (shows the diff), the file is in sync (`identical -- live and the checkout agree`), and no such file. The middle case is the normal one right after a merge, and it used to report "no match".
- **`ccs diff <path> --difftool`** opens the two sides in your own diff tool instead of printing, which is what you want for anything longer than a screen. It reads git's `difftool.*` registry -- deliberately separate from `mergetool.*`, since a name can exist in one and not the other (measured here: `bc4` is difftool-only, `beyondcompare4` mergetool-only, so reusing the merge resolver would have missed a working tool). `--tool` overrides the choice. It opens whenever the path resolves, not only when the two sides differ -- confirming a file is identical by looking at it is a legitimate reason to want the tool, and a file present on only one side opens against an empty side rather than being refused.

### Notes
- `ai_merge_command` remains **unimplemented**; `merge` suggests it and prints deterministic resolution hints instead -- regressed patterns and convention drift, which resolved the real cases here without a model.
- `apply` and `collect` have been exercised via `--dry-run` against a real payload this cycle, not run to completion; the merge paths have been run end to end.
- Analysis: `2026-07-28__14-55-26__dwp6-the-reference-model.md` names the seven references ccs holds (live, worktree, HEAD, staged theirs, seed, base, path history) and which question each answers. Nine of eleven defects found this cycle were a comparison against the wrong one.

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

[Unreleased]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.4.1...HEAD
[0.4.0]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/DazzleML/dazzle-claude-config/releases/tag/v0.1.0
