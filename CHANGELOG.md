# Changelog

All notable changes to dazzle-claude-config (ccs) are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow PEP 440.

## [Unreleased]

## [0.5.18] - 2026-09-02

### Added
- **`ccs merge <path>`, `ccs apply <path>` and `ccs collect <path>` take one file the way `ccs diff <path>` always has.** `ccs merge think/SKILL.md` used to fail with *unrecognized arguments* because only `--only` existed, and `--only` wants the repo-side label (`dotclaude/skills/think/SKILL.md`). The positional resolves a whole-component suffix against what differs and then the manifest, lists the candidates instead of guessing when more than one file matches, and then scopes the verb exactly as `--only` with the qualified label would. It also reaches a seeded file (`ccs merge settings.local.json`), which the differing-set walk alone never sees. Give one form or the other; both at once is refused, and a typo is an error that says so rather than a quiet empty run.

### Fixed
- **A bare `ccs merge` no longer opens a diff tool on a seeded file you already decided to keep.** `ccs status` reports starter files (`settings.local.json`, the machine and platform templates) through the decision record and calls a kept one *yours*; `merge` never read that record, so it planned those files anyway and put you in a three-way window whose base was the empty starter and whose live side was your real file -- one wrong-side save from wiping it. Measured on a real box: status showed two drifting entries, merge did six. Now a settled seeded file is refused visibly on an unscoped run -- `refused settings.local.json -- seeded and kept-current: yours since delivery, so not merged unless you name it` -- while a seed with a decision still pending (customised with no decision recorded, or the upstream starter moved since you decided) stays mergeable, and naming the file (`ccs merge settings.local.json`, or `--only`) is taken as consent and merges it.

### Changed
- The seed-state machine `status` uses now lives in `seeddecisions`, so `status` and `merge` read one answer; the status output is unchanged. The after-merge hint reads `ccs merge <path> does one file`.

## [0.5.17] - 2026-09-01

### Added
- **A merge you stopped partway can be continued in your tool -- when your tool can keep the work, and now ccs knows which can.** A file you already resolved reopens by itself in a tool that shows what is on disk (vimdiff, nvimdiff), with your edits in the output window. For BeyondCompare on Windows, `ccs merge --relaunch` reopens the tool and **paints your prior work back into its output pane**: it tells you first that it will take the keyboard for about a second, asks (the new `merge_inject` setting: `ask`, `always`, `never`), then reads the saved file back to verify the paint landed. It refuses before launching if BeyondCompare already has that file open -- relaunching an open file creates a hidden second session whose save prompt would overwrite your work -- and it never sends a keystroke unless focus has verifiably landed on the pane it resolved. If the paint cannot be verified and the tool saves over the file anyway, ccs puts your bytes back before validation and says `restored`. The driver ships inside the package (a PowerShell script, no new dependency) and `ccs doctor` says whether it is usable on this box.
- `ccs merge --relaunch --discard` is the old destructive reopen, spelled out so nobody reaches it by accident, and reported as `reopened WITHOUT your edits`.
- Your own `merge-tools.json` beside `ccs-config.json` overrides the packaged registry -- a tool you measured differently, a profile you corrected. A malformed overlay is reported by `doctor` and ignored; it can never change a reopen decision by accident.
- `ccs doctor` also warns when a `CLAUDE.md` of your own sits beside delivered layer files (`global.md`, `platform.md`, ...) that nothing imports -- they are unread, and the line says so once instead of leaving you to wonder why they appeared.
- The tool's exit code is printed when it is not zero (BeyondCompare's `101` means the output was not saved).

### Changed
- The "not reopened" note after a merge now says what `--relaunch` will do for *your* tool: paint your edits back in, or open it anyway.
- `merge.py`'s docstring no longer claims ccs has "no merge engine"; git's diff3 does the three-way, and the coming `--ai` path is a resolution workflow above it.

## [0.5.16] - 2026-09-01

### Added
- **ccs now knows what each merge tool does with a file you already resolved.** Whether reopening a tool over an existing merge result preserves it is a fact about the tool -- vimdiff shows what is on disk, BeyondCompare regenerates its output pane from the three inputs and discards it -- and until now nothing in ccs had that written down. A packaged registry (`merge-tools.json`) declares it per tool: `preloads`, `writes-only`, or an injection profile for tools ccs will be able to paint a result back into. A tool configured under any name (`bc2`, `bc5`, `beyond`, whatever your git config calls it) is classified by the binary it runs, since one name can point at two versions -- and a tool nobody has classified is treated as the safe case, never as an error. The registry ships as package data and is proven present inside the built wheel, the same guard the settings explanations have; if it ever fails to load, ccs falls back to a built-in table that a test holds identical, so a packaging slip can cost profiles but never change a reopen decision. Nothing in the merge flow reads the table yet -- that is the next release; this one makes the knowledge exist and ship.
- **`docs/walkthrough.md`**: a first machine from nothing, the payload choices, the `CLAUDE.md` choice (a plain file of your own is fully supported -- nothing forces the layered template), a second machine, and a box that forked before the payload existed -- with an observable outcome at every step so the page doubles as a test. Linked from the README.
- The merge-tool probe scripts gained the launch-then-locate experiment (`p1_*`) that measured how BeyondCompare behaves when a file it already has open is launched again: a second, hidden session appears and its close-time save prompt would overwrite the saved work. The results file is the design input for the injection driver.

### Fixed
- The injection proof-of-concept script no longer writes a space to the clipboard when the clipboard was empty before it ran; an empty clipboard is restored by clearing it.

## [0.5.15] - 2026-08-31

### Fixed
- **`ccs merge` no longer reopens a file you already resolved -- which was destroying the resolution it had just promised to keep.** The tool is handed the merged file as its *output* pane, and the common merge tools (BeyondCompare's documented behavior, identical in BC4 and BC5) regenerate that pane from the three inputs on every load, discarding whatever the file held. So a re-run correctly detected your saved work, reported it kept, then launched the tool over it -- and the whole "stop partway and come back later" workflow was impossible, because every re-run re-created the work already done. Resumed files are now left closed; a new `--relaunch` flag opts back in for tools that do load their output pane (vimdiff does).
- When files are held closed, the output says why -- "your tool is handed the merged file as its OUTPUT and would regenerate it over your edits" -- and names `--relaunch`, so the missing window reads as a deliberate refusal rather than a failure.
- The install hint after a resumed run now says `ccs merge --accept --no-launch`: plain `--accept` still launches the tool, which would regenerate and install *its* merge instead of yours. That distinction cost a real evening before it was written down.

### Changed
- The resumed line now claims only what the bytes prove: "differs from the generated seed; keeping it as yours" rather than "kept your prior edits" -- whether a human made the file differ is an inference, and a tool-saved pane reads the same as a hand edit. Telling those apart is the resume record's job, tracked separately.

## [0.5.14] - 2026-08-28

### Fixed
- **`ccs seed migrate <file>` was applying the whole payload.** The verb takes the payload's newer version of one starter file and proves that one file survived intact -- but underneath it ran an unrestricted apply, so every other entry was copied and every absent starter seeded too, silently and unreported. Measured on a three-entry fixture: migrating `CLAUDE.md` also wrote `NOTES.md` and a skills file nothing had asked for. It matters most in the seed walk, where working through several starter files one keystroke at a time meant a full apply per keystroke. The verb now touches only the entry it names.
- `ccs apply --dry-run` no longer prints "backed up to None" for each retired file. A preview has no backup directory yet, and the line was interpolating one anyway -- on a real migration that read as a broken tool across 23 files. The preview now says the file *would be* moved, and names the directory it would be created under, which it previously never did.
- The backup directory is named once, in the summary line, rather than repeated on every removed file. The same run printed the identical path 23 times before printing it again at the end.

## [0.5.13] - 2026-08-28

### Added
- **`ccs doctor` now tells you the state of your config file, not just whether it parses.** A config file can be perfectly valid and still be missing five settings the version you are running knows about -- and those settings govern your machine anyway, at their defaults, with nothing in the file you would open to find out why. Doctor names them, and names the command that adds them. Measured on a real second machine: it held 6 of the 11 settings, and the 5 it lacked included the one that moves files.
- Having no config file at all is reported as **fine**, because it is -- ccs is designed to behave safely with none. It is still mentioned, along with the command that writes one, since "safe" and "visible" are different things.
- A setting in your file that this version does not recognise is reported and left alone. It usually means a newer ccs wrote the file, and it can also mean a typo that is silently doing nothing, so it is worth a look either way.

### Changed
- **`ccs status` no longer calls a checkout "clean" while it holds commits nobody else has.** It already refused to say "clean" when the checkout was *behind*; being *ahead* is the worse of the two to stay quiet about, because behind means the work reaches you on the next pull while ahead means it exists on exactly one machine. The summary now says how many commits are unshared and names `ccs git push`.
- A checkout that has **diverged** from its remote is told so, and is no longer pointed at `ccs status --pull` -- that only ever fast-forwards, and would refuse.
- A config file that cannot be read is described in words you can act on: "not valid JSON" rather than the parser's own `JSONDecodeError`. A file that is valid JSON but not an object (a list, say) says *that* instead, since hunting for a syntax error that is not there wastes real time. Every command now says it the same way -- `status`, `apply` and `collect` were still printing the raw exception name while `doctor` and `setup update` used plain language, so the same broken file was described two different ways depending on which command you happened to run.
- The warning about a file that git ignores now says both halves of what happens to it. It never commits, so no other machine ever receives it -- and because `apply` reads the checkout's working tree rather than git, this machine keeps re-installing it every run. Reading only the first half, a reasonable person concludes the file is inert; it is not, it is syncing in a loop version control cannot see.

### Fixed
- **`sync_removals: "all"` no longer tells you a file was unmodified when it held your edits.** Setting it to `all` stages every retired file, including one you had changed; the report said "your copy was unmodified" regardless. The file was backed up byte-for-byte either way and nothing was ever lost, but someone whose edited file had just disappeared had no way to learn from that line that their edit was there at all. It now says your edits went with it, and names the setting that caused it.
- Every verdict in `ccs status` starts its filename in the same column. `checkout` and `live only` were padded to a different width than the per-file verdicts, so in an entry holding both kinds the names sat one column apart and stopped forming a column.
- The links in the generated settings page now reach the sections they name. Eight of the eleven were dead, because the link text converted underscores to hyphens and the headings did not.

## [0.5.12] - 2026-08-27

### Added
- **[A page listing every setting](docs/configuration.md).** It is generated from the same words `ccs setup update --explain` prints, so it cannot describe a version of ccs that never shipped -- which is the failure that made a hand-written page not worth having. A test regenerates it and fails if the committed copy has fallen behind, so the two cannot disagree.
- `ccs doctor` now reports whether the settings explanations are readable, and names any that are missing. This check is unlike the rest of doctor's: everything else it looks at can only break through something you did, while this can break through a packaging mistake that is invisible in a source tree and appears only on a machine somebody installed ccs on.

### Changed
- **The explanations moved out of the code and into a file that can be edited on its own.** They now live in `settings-explanations.json` inside ccs itself, so the wording can be improved without touching Python, the settings page can be generated from it, and a translation is a sibling file. They still ship inside the package, so `ccs setup update --explain <setting>` answers on a headless box over SSH with no browser and no network, exactly as before. What a setting *does* -- its default, its valid values, the environment variable that sets it -- deliberately stayed in the code: losing the explanations file must leave ccs unexplained, never leave it without its defaults.
- **`ccs setup update --explain` with no setting named is now a short index rather than every word at once.** It lists each setting with its default and a one-line summary, names the page above, and offers to print everything in your terminal. The previous behaviour printed about eighty lines, which scrolled its own beginning off the screen and left you nothing to do about it. Naming a setting is unchanged, and that is the form that matters: `ccs setup update --explain sync_removals` still prints it in full, immediately.
- Redirecting or piping `--explain` gives you everything and never stops to ask a question. Sending output to a file or a pager is a request for the content, and nothing scrolls away in a file. This also fixes a real hang: the previous test for "is a person watching this" answered yes for a redirected command on Windows, so a piped run could stop on a prompt nothing could see or answer.

### Fixed
- Truncated summaries end in three ordinary periods rather than a single-character ellipsis, which the Windows console draws as a placeholder box on its default codepage.
- `CCS_INTERACTIVE` is honoured when its value has spaces around it, which is what a shell script or a build system templating the value tends to produce.

## [0.5.11] - 2026-08-27

### Added
- **`ccs setup update` -- your config file learns what the new version knows.** A config file froze at the settings of whatever version wrote it, and nothing ever updated it, so a machine could run a release for weeks while its own config described an older tool. That was a nuisance until 0.5.10 added a setting that MOVES FILES, at which point a machine could acquire a file-touching behaviour on upgrade with nothing in its config recording that the setting exists. The command adds each setting the new version knows at its documented default, and does nothing else: a value you set is never changed -- not even one that happens to equal the default, because a key holding the default is indistinguishable from one ccs wrote and both are yours. Your keys keep their positions and the new ones are appended after them, so a five-setting addition is a five-line diff rather than a reordered file.
- A key this version does not recognise is **reported and left exactly where it is**. It usually means a newer ccs wrote the file, and removing it would throw away a setting that version wants back the next time it runs.
- `--dry-run` prints exactly which settings would be added, with their defaults, and writes nothing.
- **`ccs setup update --explain [SETTING]` says what a setting means** -- its default, its valid values where it has them, and the environment variable that sets it. With no setting named, all of them. It reads the same words the tool itself uses, so it cannot fall behind what it describes; a config file cannot hold explanations, and this is where they live instead. It writes nothing and needs no config file, so a new machine can ask before it has anything.
- A config file that cannot be parsed is **never written over**. It may hold settings you care about, and rewriting it to fix a typo would destroy them; ccs says what is wrong and what to do about it instead.

### Changed
- **Every setting now explains itself.** What each one means used to live in comments beside the code, which meant "what does this key do?" could only be answered by reading source. The explanations are now part of the settings table the program actually reads, so the same words reach you wherever you ask -- and a setting added without an explanation now fails the test suite instead of shipping unexplained. This replaced the alternative of writing the explanations down a second time somewhere else, which drifts: the release before this one added a setting, so any separate documentation written the previous day was already wrong about it.


## [0.5.10] - 2026-08-26

Four defects in the `apply` / `collect` chain, fixed together because they share one root cause: content alone cannot distinguish "you changed it" from "you never received it". Every one of them was a place where ccs guessed that question and acted on the guess.

### Fixed
- **A file that was merely out of date in your live config was reported as being AHEAD of the checkout, and `apply` told you to run `ccs collect` on it** -- which would have overwritten the newer committed version with the older one. Nothing had edited the file; a change had been committed in the checkout and never applied. A side that holds no lines the other side lacks cannot be ahead, so ccs now says so plainly: such files are labelled `unattributed`, the entry above them reads `direction unproven` rather than claiming everything under it is one-sided, and both name staleness as the likelier reading and point at `ccs diff` rather than at a verb that writes. The check applies only where the direction was estimated -- when your live file exactly matches a commit, the direction is known, and an upstream retirement that leaves the checkout holding nothing unique is still reported correctly as the checkout being ahead.
- **`apply` put back files you had deleted on purpose, every time it ran.** Whether an absent file was deleted deliberately or never arrived cannot be answered from the checkout -- its history contains every file it has ever had, either way -- so ccs stops guessing and asks. It now tells you when it installs a file your live config did not have, and `ccs apply --keep-deleted <path>` records your answer in `~/claude/ccs-deleted.json` so it stops coming back. `--restore-deleted` undoes that. The record is ordinary editable JSON with a comment explaining what it is.
- **`ccs-config.json` was ignored entirely if your editor saved it with a byte-order mark.** Every setting silently reverted to its default -- `"auto_pull": true` was read as false -- and the error explaining why was recorded somewhere nothing ever printed. The file is meant to be edited by hand, and the editors people reach for on Windows add that mark. It is now read the same way as every other file ccs keeps in your own directory, and a genuinely broken config warns, exactly as a broken box file always did.
- **A retired file was reported forever instead of being cleaned up.** When the payload deliberately deletes a file, every machine kept its copy until someone ran `ccs apply --sync-removals` by hand, so a half-migrated box could carry both the old command and the new skill, and both would load.

### Added
- **`sync_removals`, a three-way setting for files the payload has retired.** `untouched` (the new default) moves a retired file into the backup directory only when your copy still matches a committed version -- such a copy holds nothing of yours, so nothing of yours is lost. A copy you edited is reported and kept, with both ways forward named. `all` stages every retired file; `never` only reports. `--sync-removals` and `--no-sync-removals` override the setting for one run in either direction, and `CCS_SYNC_REMOVALS` sets it per shell.
- Retired files are never removed automatically from a checkout that is not on a branch, or that is behind its remote. On a stale tree everything added since looks retired, and the automatic policy would clear it in one pass. An explicit `--sync-removals` still works; only the automatic behaviour stands down, and it says why.
- An unrecognised value for `sync_removals` falls back to `never`, the safest of the three, rather than to the default. A typo must never widen what the tool deletes.


## [0.5.9] - 2026-08-26

### Fixed
- **`collect` resurrected the vacated side of a rename.** `git mv a.md b.md` leaves the checkout with `a.md` absent while your live config still holds it, and `collect` read that as an ordinary new file and copied it back -- exit 0, reported as a plain `copied:`, leaving the checkout with both names and the rename half-reverted. A rename is one edit with two halves: the new path holds the content, the old path's absence is the rest of it. Both are now protected. This was the same class of silent loss the guard was written to prevent, reintroduced through the guard itself, and it was found by a human-run checklist rather than by the suite.
- **`apply` claimed your live config already matched while printing the failure that proves it did not.** A file that could not be written -- a read-only target, a permissions problem -- was left out of the check that decides whether anything was held back, so the summary printed "your live config already matches the checkout" directly beneath the line naming the file it had failed to write. The exit code was correct throughout; only the sentence was wrong.
- **`ccs seed migrate` told you to run a command that no longer exists.** Listing what could be migrated printed the hint `(ccs migrate <file> keeps your copy, then proves it)` -- the pre-rename spelling, which stopped parsing one release earlier. The rename updated the actionable hints elsewhere and missed this one. A test now scans the package for the old spelling instead of pinning a single line, so the next rename cannot leave a reachable string behind either.
- **`collect` protected a renamed checkout file under its old name.** A staged rename reports `old -> new`, and the uncommitted-work guard recorded the path on the left. The file `collect` would actually overwrite is the one on the right, so a renamed file was left unguarded -- and reorganising a payload is mostly renames. Found by mutation testing.

### Changed
- **The remote line says which side is ahead.** `main, 2 ahead` did not tell you whether your checkout had commits to push or the remote had commits to pull, and the two call for opposite actions. Every state now names one subject: `your checkout is 2 ahead -- ccs git push to share`, `your checkout is 3 behind -- ccs status --pull`, `your checkout is 2 ahead and 3 behind -- diverged`. The wording matches the summary line, which already named the side.
- README documents `ccs git <any git command>` and the `auto_pull` setting, which shipped in 0.5.1 without ever appearing there, and the `collect` refusal from 0.5.8.

## [0.5.8] - 2026-08-26

### Fixed
- **`collect` no longer overwrites an uncommitted edit in the checkout.** The checkout is an editing surface, not only a mirror -- settings are edited there by design, and reorganising a payload is hours of working-tree changes. A file whose working-tree copy differed from HEAD was overwritten with the live version, silently, exit 0: work that existed in no commit and on no other machine, gone with a success message. `collect` now refuses that file, names the three ways out (commit it, discard it, or `--force`), collects everything else, and exits nonzero because something you asked for did not happen. Untracked files are protected the same way -- `git diff` would not report them, and losing an uncommitted draft is losing it either way. One `git status` for the whole run, not one call per file.
- `collect`'s "nothing to do" line no longer claims the checkout already has everything when a file was held back.

## [0.5.7] - 2026-08-25

### Changed
- **`ccs migrate` is now `ccs seed migrate`.** It only ever acted on starter files -- it resolves its target through the seed entries and refuses anything else -- so it belongs beside `keep`, `reset`, `list`, and `diff` rather than beside the verbs that sync your whole configuration. Bare `ccs seed migrate` still lists what this box could migrate; `--dry-run` still writes nothing. The verb was one release old and unreleased to PyPI, so nothing outside a development box could depend on the old spelling.
- **The starter-file hints now point at the verified path.** Where they used to say `ccs apply --reseed <file>` -- the raw operation -- they say `ccs seed migrate <file>`, which does the same reseed but keeps a copy of your version outside the backup tree first and proves both copies hold your original bytes afterwards. `apply --reseed` remains as the low-level escape hatch it always was.

## [0.5.6] - 2026-08-25

### Fixed
- **`apply` refused to install a checkout file that was never committed, and blamed the checkout for it.** A file you create in the payload, apply, then edit -- the ordinary way to author one -- was skipped on the second apply as "an uncommitted local snapshot, older than live", a direction git cannot supply for a path with no history. The both-sides guard exists to stop a one-way copy from discarding *committed* work; an uncommitted checkout file has none to protect, and the live copy is backed up like any other overwrite. It applies now, and `status` says plainly that history cannot tell which side is newer.
- **`apply` claimed "your live config already matches the checkout" when it did not.** Any run that copied nothing said so, including runs that skipped files for direction or reported pending removals. The summary now says what was held back instead of asserting a match that the same output contradicts.
- **A committed but empty file was classified as never committed.** `git show HEAD:<path>` succeeds with empty output for an empty tracked file, and the emptiness was being read as absence; only the return code decides now. Found by mutation testing -- the mutant that survived was pointing at a real defect.

### Changed
- When the checkout is behind, `status` now names the command that acts: `ccs status --pull` fast-forwards and re-checks in one step, and the line mentions the `auto_pull` setting that makes it permanent. The old advice ("git pull, then run status again") predated both, so the feature existed for a release without ever being mentioned where it was needed.

## [0.5.5] - 2026-08-25

### Fixed
- **`ccs migrate` wrote its backups to the real home directory, ignoring `--user-claude`.** The migration derived its backup root from the interpreter's home rather than from the run's own territories, so a run pointed at a scratch tree still put both the kept copy and the apply backup into the operator's actual `~/claude/backups/`. Every other verb derives it from the run's territories; this one now does too. Found by a checklist run that wrote six real files before noticing -- and the automated tests had not caught it because they set `HOME` to the scratch tree, which made the wrong path coincide with the right one. Those overrides are gone, and a test now pins where both copies land.

## [0.5.4] - 2026-08-25

### Added
- **`ccs migrate [file]`** -- take the payload's newer version of a starter file you already own, safely and verifiably. It hashes your live file, keeps a copy of it outside the apply backup tree (written by a different code path, so one bug cannot quietly spoil both copies), takes the payload's version, and then PROVES the result: both copies must hash to your pre-migration bytes and the live file must match the payload's. Bare `ccs migrate` lists what this box could migrate; `--dry-run` says what would happen and writes nothing. This is the sequence two real machine migrations ran by hand -- and on the second one the proof step was silently skipped, which is exactly why the tool performs it now.
- **`ccs seed`** with no arguments walks every open starter-file question one keystroke at a time -- keep mine `[k]` / keep always `[a]` / take the payload's `[t]` (runs the verified migration) / open both in my diff tool `[d]`, which re-asks the same file afterwards / skip `[s]` / quit `[q]`. Terminal only; piped runs say so and exit without prompting.
- **`ccs seed diff <file>`** opens your copy against the payload's in whichever diff tool ccs can resolve -- the status line said "open both files in your diff tool" and then left you to find the binary yourself.

## [0.5.3] - 2026-08-25

### Changed
- `ccs --version` (and its new shorthand `-V`) now shows the full build string alongside the release version -- `ccs ALPHA 0.5.3 (0.5.3_main_35-20260825-05904f1f)` -- so two builds of the same release are distinguishable at a glance (branch, build number, date, commit). Mirrors the sibling tools' format.
- Project practice from here on: every commit bumps at least the patch version, so no two installable states share a number.

## [0.5.2] - 2026-08-25

### Added
- **`ccs setup box`** -- declare this machine's identity: the name and tags that decide which tag-gated entries apply here. Flags for scripts (`--name`, repeatable `--tag`), a prompt for humans, a report when the declaration exists -- and it NEVER overwrites one. Two real machine migrations reached `apply` before anyone noticed the declaration was missing; now the tool can create and verify it.
- **`ccs doctor`** -- read-only environment check: interpreter and git versions, the checkout (present, a repo root, remote configured and reachable), the box declaration, user config, the manifest, and any open seed questions -- each finding with the command that fixes it. Prints and changes nothing, so it is safe on boxes with a hands-off policy. Exit 0 healthy / 1 warnings / 2 unusable.
- **`ccs seed keep | reset | list`** -- seeded files are yours after delivery, and when yours differs from the payload's current seed that is a question, not drift: keep yours `--always`, keep `--until-changed` (asks again only when the upstream seed actually moves -- anchored to a hash of the seed you decided against, never a date), or take the fresh seed with `apply --reseed`. Decisions live in a hand-editable `~/claude/ccs-seed-decisions.json`.
- **Directory seeding**: a `seed-if-absent` entry whose repo path is a directory now seeds every absent file under it, recursively -- an existing live file is never touched, the deny-list applies per file, `--only` scopes inside the directory, and `--reseed` names a single file within it.

### Changed
- Bare `ccs setup` now runs the doctor check instead of an argument error -- landing new users on "what is configured, what is not, and the command that fixes each". `ccs doctor` remains the conventional read-only name.
- Seed lines in `status` and `doctor` speak plain language ("delivered once as a starter, then yours -- and yours now differs from the payload's version") and print BOTH file paths -- your copy and the payload's -- so "open them in your diff tool" is actually possible. `ccs seed -h` gained a glossary: what "seeded" means, where every file lives (including the decisions file), and the three answers.
- **`status` stops overclaiming about seeded files**: the clean verdict now says "everything ccs syncs matches" and counts seeded files that are yours and not compared; a seeded file that is an UNMODIFIED older version of a seed the payload has since replaced gets an automatic pointer to `apply --reseed` (no question asked -- you never edited it); a customized one gets the yours-or-the-payload's question once, then respects your recorded answer. Comparison is line-ending-insensitive throughout -- measured on a real migration, a raw comparison matches nothing on Windows.

### Fixed
- `apply --dry-run` no longer announces seeds and reseeds as if they had happened ("the fresh seed is live") -- it now says `would seed` / `would reseed`, matching the copy path's wording. Nothing was ever written; only the sentence overclaimed.
- A `seed-if-absent` entry pointing at a directory used to parse cleanly, count as matched, and silently deliver nothing -- the silent narrowing the default-closed manifest exists to forbid. Directory entries now seed (see Added); the silent path is gone.

## [0.5.1] - 2026-08-24

### Added
- **`ccs git <anything>`** -- run git in the checkout, from any directory: `ccs git pull`, `ccs git push`, `ccs git log --oneline`. Exactly `git -C <checkout>` with the checkout resolved the way every verb resolves it; arguments, stdio, and the exit code pass through untouched, so pagers, prompts, and credential helpers behave as if you had cd'd there. Everything after the word `git` belongs to git -- ccs never mistakes git's flags for its own. Bare `ccs git` prints where the checkout is. The home-repo safety guards hold here too.
- **`auto_pull`** (`~/claude/ccs-config.json`; `CCS_AUTO_PULL`; per-run `--pull` / `--no-pull`) -- `status` closes the loop it used to describe: when the fetch finds the checkout behind and fast-forwardable, status fast-forwards first and then reports the real drift in the same run, saying `was 2 behind -- fast-forwarded`. Strictly fast-forward-only: a divergent branch or a dirty file in the way is reported in git's own words, never merged, rebased, or stashed. Off by default; status-only -- `apply` and `collect` keep their existing warning.

### Changed
- **`status` shows the remote as its own labelled leg** (#22): a `remote` line with the host and pull state (`github.com/you/your-payload: main, in sync`) above `checkout`, which now carries its branch in parentheses. The remote is a place too -- the hub every other machine syncs through -- and "is there anything on the server my machines have not seen?" now has its own line instead of riding as a clause under a folder path. Hints on that line name the new verb (`ccs git pull`).
- `collect` and `apply` help lines now lead with their direction (`live -> checkout` / `checkout -> live`), and `ccs -h` states the rule once: directions are named from the payload's side -- the checkout collects from a box; its contents apply to a box.

## [0.5.0] - 2026-08-24

### Added
- **`apply --reseed TARGET`** -- the migration move for a box whose file predates a seed. A `seed-if-absent` entry never overwrites, which is right until the day the payload ships a *fresh* seed you actually want (a new pointer-style `CLAUDE.md`, say) and your box still has the old hand-written file. `--reseed CLAUDE.md` backs the existing live file into this run's backup directory and writes the payload's seed over it -- one command instead of "move your file aside by hand". One entry per run, exact target or repo path, `--dry-run` honoured, and a warning when it matches nothing.

## [0.4.3] - 2026-08-22

The release that lets a machine which forked a year ago join the fleet without losing a line it wrote. Built against a real production machine's `CLAUDE.md` (measured read-only over a network mount): its true ancestor lived in another repository, and a correct three-way merge against that ancestor silently dropped 166 lines of the machine's own operating manual, while the validator refused the same merge for the rule upstream had retired on purpose. Four changes, in the order a new machine meets them: an entry can say which machines it belongs to; `--only` can name a subtree; the validator consults the ancestor; and a merge can be handed an ancestor from outside the checkout, shown what it would let go of, and reviewed hunk by hunk.

### Added
- **Adopting a machine whose file forked before the payload existed: supply the ancestor, see what the merge would let go of, review every deletion.** `ccs merge --base-file FILE` (or `--base-from REPO[@SHA]:PATH`, read straight out of another git repository -- typically the home repo of the machine that seeded the box) merges against an ancestor the checkout's history does not hold. A supplied base is a fact, not an estimate, so the sibling check that guards inferred bases does not run on it; instead the merge is seeded **conflict-on-delete**: every region the payload removed since the ancestor that this box left untouched becomes a reviewer hunk (the region on the ours and base panes, nothing on theirs) rather than vanishing. On the real forked file that turned 166 silently dropped lines into 24 hunks with nothing lost. `--block-swap-ratio` tunes when a rewritten region counts as a removal (default 0.6). `--accept` on such a merge writes the **live file only** and leaves the checkout at HEAD -- installing a box's own sections into the shared payload would publish them to every other machine and make them the next base, which is the mechanism that deletes them on the following merge -- and prints `record <base> as this box's base`. `ccs diff <path> --difftool 3 --base-file FILE` opens the same three-way view for a look.
- **`ccs merge --dry-run` now shows the base table.** Under each `would merge` line it prints, for the inferred ancestor and any supplied one, how far each is from both sides, the sibling verdict, how many hunks the merge would produce, and per side how many unique lines would go silent, how many of those are honoured deletions, and how many would be truly lost. The last number must be 0; it is the figure every adoption should be checked against before anyone opens a diff tool. Same code the merge seeds from, so the table and the merge cannot disagree. (A separate `ccs base` verb was built first and folded into the dry run before release: the pre-merge look is the dry run of the merge.)
- **Manifest entries can be limited to the boxes that declare a tag.** An entry with `"tags": ["prod-vps"]` applies only on a machine whose `~/claude/ccs-box.json` declares that tag -- and `collect` honours the same rule, so a file that belongs to one box is never staged into the shared checkout by a box that lacks the tag. Tags are names you choose, not hostnames. A missing box file means no tags, which switches tagged entries off; a malformed one is reported and also means no tags, so a broken declaration can only narrow what syncs, never widen it. `ccs status --long` lists the entries the gate kept off this box and why (`not for box devbox ... -- needs tags: prod-vps`). `CCS_BOX_TAGS` overrides the file for one run. This is the mechanism behind per-machine files (`machines/<name>/`) and opt-in addenda, which until now were directory conventions the tool never read.

### Changed
- **`vimdiff`, `nvimdiff`, `meld` and `kdiff3` work with no git configuration.** `ccs merge` used to require a `mergetool.<name>.cmd` entry for every tool, which git's built-ins do not have -- so on a server with vim and nothing else, `merge` stopped with "no usable merge tool found" despite the README's promise. The four common built-ins now carry git's own invocations inside ccs and are probed like any configured tool (usable only where the binary is); a configured entry always wins. `docs/merge.md` is new: what the three sides are, how to read the dry-run table and the install receipt, adopting a machine that forked before the payload existed (including where the ancestor comes from), merging on a box with no GUI, and what each refusal means. `docs/three-way-merge.md` is the primer for anyone who has never resolved one in a visual tool -- the window, the four moves, and what it looks like in Beyond Compare, vimdiff, VS Code and friends.
- **`--only` matches whole path components and can reach inside an entry.** `--only dotclaude/skills/test-mutation` now scopes `apply`, `collect`, `merge`, the two-sided refusal, `--force`, and the direction skips to that one subtree (or one file) and nothing else; before, `--only` was a string prefix on entry names, so that command matched no entry at all -- silently -- and `--only dotclaude/sk` matched `dotclaude/skills` by accident. A partial component now matches nothing and says so. The two-sided guard also honours box tags: an entry this box is not tagged for is never refused on, since it is never copied.

### Fixed
- **A merge can now install a result in which one side's deleted lines stay deleted.** The validator used to refuse any result missing a line that only one side held -- without ever looking at the base. So when the payload deliberately retired a rule, every box that merged a two-sided `CLAUDE.md` got a correct merge and a refusal, and nothing retired upstream could ever land. With a base, a missing line is now fine when the base holds it and the other side does not: that side deleted it on purpose, and a three-way merge is right to honour that. A line one side *added* that the result lacks still fails -- that is the loss the gate exists for. On install the tool lists what it honoured, per side, with the first line of each region (`retired upstream: 48 lines in 4 regions -- ## Postmortem Commands ...`); that list is also the alarm for a wrong base, since a box seeing its own headings there has merged against an ancestor that never held them. Measured on the real forked file: refused before, installed now, zero lines lost.
- **Without a base, a dropped line is shown and asked about instead of refused outright.** When no ancestor exists the tool cannot tell a deliberate deletion from an accident -- but the person who just resolved the file in their diff tool can. If the only failure is dropped lines and a human resolved the file (a tool was launched, or the workspace file carries edits), the lines are printed and `install it anyway? [y/N]` is asked; non-interactive runs never accept. A fresh, untouched seed is never asked about.
- **A typo in an entry's `os` is refused instead of silently never applying.** `"os": "linux"` or `"os": "Windows"` used to parse fine and then match no machine at all, removing the entry from every box without a word. Only `windows` and `posix` are accepted now.

## [0.4.2] - 2026-08-21

`ccs status` now looks at the remote before it says "in sync". The first two-machine round trip read `in sync with origin/main` only because the operator had run `git fetch` by hand minutes earlier: the line came from `git status -sb`, which compares against whatever the last fetch left behind, and nothing in ccs had ever fetched. A stale tracking ref makes every checkout read as current -- the same confident-claim-without-evidence this tool spent 0.4.1 removing one layer down.

### Added
- **`status` fetches the upstream first.** One `git fetch` per run, against the branch's own remote, touching remote-tracking refs only -- no local branch, no index, no working tree, so `status` stays read-only and is safe mid-edit. The branch line then says `2 behind vs origin/main -- git pull`, and the verdict refuses to call the tree clean while a pull is waiting: `status: live matches the checkout -- but the checkout is 2 behind origin/main`, exit 1. When the fetch cannot run, the negative is withheld rather than faked: `pull status unknown -- fetch failed: could not resolve host (vs origin/main as last fetched)`. A fetch that would prompt for credentials fails fast instead (non-interactive by construction), and a stalled remote hits a timeout (15 s by default) rather than hanging a read-only verb.
- **`--no-fetch`**, and `fetch` / `fetch_timeout` in `~/claude/ccs-config.json` (or `CCS_FETCH` / `CCS_FETCH_TIMEOUT`), for air-gapped or metered machines. With fetching off, the line is labelled: `in sync with origin/main as last fetched`.
- **`apply` and `collect` say when the checkout is behind** -- `note: checkout is 1 behind origin/main -- applying what is here; git pull first for the other machine's latest` -- and proceed. Nothing is lost either way, and "install what I have here, now" is a legitimate intent. **`--require-current`** (or `require_current` in the config) turns the note into a refusal for anyone who wants the pull-first loop enforced; a *failed* fetch never refuses, even then, because a tool that stops working when the network is down is not a sync tool.

ccs still does not pull or push. It tells you whether you need to.

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

[Unreleased]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.5.18...HEAD
[0.5.17]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.5.16...v0.5.17
[0.5.16]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.5.15...v0.5.16
[0.5.15]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.5.14...v0.5.15
[0.5.14]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.5.13...v0.5.14
[0.5.13]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.5.12...v0.5.13
[0.5.12]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.5.11...v0.5.12
[0.5.11]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.5.10...v0.5.11
[0.5.10]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.5.9...v0.5.10
[0.5.9]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.5.8...v0.5.9
[0.5.8]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.5.7...v0.5.8
[0.5.7]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.5.6...v0.5.7
[0.5.6]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.5.5...v0.5.6
[0.5.5]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.5.4...v0.5.5
[0.5.4]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.5.3...v0.5.4
[0.5.3]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.4.3...v0.5.0
[0.4.3]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/DazzleML/dazzle-claude-config/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/DazzleML/dazzle-claude-config/releases/tag/v0.1.0
