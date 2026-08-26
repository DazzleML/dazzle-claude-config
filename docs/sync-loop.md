# The sync loop

The whole tool is one loop: **bring changes in, work, send changes out, share.** Everything else is a guard around a step of it.

```
  GitHub ──git pull──► checkout ──ccs apply──►  ~/.claude   (bring changes IN)
                                                    │
                                              you edit config
                                                    │
  GitHub ◄─git push─── checkout ◄─ccs collect── ~/.claude   (send changes OUT)
```

Three locations, and the distinction that matters: **your live config is what Claude Code reads; the checkout is a git clone Claude Code never looks at.** Editing the checkout changes nothing until you `apply`.

## The loop, step by step

```bash
export CCS_CHECKOUT_DIR=~/claude/my-config      # or pass --checkout-dir every time

# 1. get what other machines sent -- `ccs status` tells you when this is due
git -C "$CCS_CHECKOUT_DIR" pull

# 2. look before you leap -- nothing is touched (it fetches, so "in sync" means the remote)
ccs status

# 3. reconcile anything that changed on BOTH sides (see below)
ccs merge

# 4. bring the rest in
ccs apply

#    ... work. edit skills, commands, CLAUDE.md, whatever ...

# 5. send your changes out
ccs collect

# 6. share them (ccs git == git -C <checkout>, from any directory)
ccs git add -A
ccs git commit -m "config: ..."
ccs git push
```

Steps 1 and 6 are still plain git -- `ccs git <anything>` is exactly `git -C <checkout>`, arguments and exit code untouched, so nothing about where your config lives is hidden; it only saves the cd. `status` tells you when step 1 is due: it fetches the upstream before it reads the branch state (remote-tracking refs only -- nothing you own changes), and reports it on its own `remote` line, so `main, in sync` is a claim about the remote, `main, 2 behind -- ccs git pull` means exactly that, and if the fetch cannot run it says `pull status unknown` rather than guessing. With `auto_pull: true` in `~/claude/ccs-config.json` (or `--pull` for one run), status closes the loop itself: when the checkout is behind and fast-forwardable it fast-forwards first -- strictly `--ff-only`; a divergent branch or a dirty file is reported in git's own words, never merged, rebased, or stashed -- and then reports the real drift, saying `was 2 behind -- fast-forwarded`. `apply` and `collect` print a note when the checkout is behind and carry on; `--require-current` makes them refuse instead. `--no-fetch` (or `fetch: false`) skips the network, labels the line `as last fetched`, and suppresses any pull.

## Seeded files: delivered once, then yours

A `seed-if-absent` entry delivers a file to a box that lacks it and never touches it again -- after delivery the file is the box's own. `status` therefore does not count a changed seeded file as drift; instead it tells the truth about what that means: the clean verdict reads "everything ccs *syncs* matches" and notes how many seeded files are yours and uncompared. When yours differs from the payload's current seed, that is a question, not a defect -- `ccs seed keep <file> --always` or `--until-changed` records your answer (hand-editable in `~/claude/ccs-seed-decisions.json`; `ccs seed reset` revokes), and `ccs apply --reseed <file>` takes the fresh seed with your copy backed up first. One case needs no question at all: a seeded file that is an unmodified OLDER version of a seed the payload has since replaced -- status points straight at `--reseed`, because you never edited it. A directory seed entry seeds every absent file under it and never overwrites an existing one.

When the payload ships a NEWER starter for a file you already own, `ccs seed migrate <file>` does the whole move and proves it: your live file is hashed, a copy of it is kept outside the apply backup tree, the payload's version is installed (`apply --reseed`, which makes its own backup), and then both copies are verified against your pre-migration bytes. Bare `ccs seed migrate` lists the candidates; `--dry-run` writes nothing. When several starter files need answers at once -- what a payload restructure produces -- bare `ccs seed` walks them one keystroke at a time, with `[d]` opening your copy against the payload's in your own diff tool before you decide.

## Why step 3 exists

`apply` and `collect` are **one-way copies**. That is fine when only one side changed. It is not fine when both did. `status` tells the two cases apart by asking the checkout's history which commit each side still equals, and shows its evidence:

```
ccs status --long
  dotclaude/CLAUDE.md: differs -- both sides (both moved since 4c2935a) -- 2 lines only in live,
                       6 replaced, 50 lines only in the checkout, in 10 regions
  dotclaude/skills: 3 files differ (all one-sided)
      one-sided   create-project/SKILL.md  ...  -- checkout ahead; live == 4c2935a
      one-sided   oracle/SKILL.md          ...  -- live ahead; checkout == f2a9249
```

`checkout ahead; live == 4c2935a` means live is byte-for-byte the file as of that commit: nothing on the live side is unique, so copying checkout over it loses nothing. `both moved since 4c2935a` means neither side equals any commit: both hold unique work. When no commit matches either side, `status` says so (`no ancestor in history attributes this change`) and treats the file as two-sided, because unknown is not the same as safe.

A one-sided file is safe for **one** verb, not both. `apply` skips files where live is ahead and `collect` skips files where the checkout is ahead, each with a reason:

```
ccs apply
  skipped skills/oracle/SKILL.md -- live is ahead -- nothing to apply; `ccs collect` it
  applied skills/create-project/SKILL.md
```

Copying either direction on a two-sided file discards whatever the losing side added. So `apply` and `collect` **refuse** those files rather than quietly picking a winner (`--force` overrides both the refusal and the direction skips -- it really does overwrite everything):

```
ccs apply
  REFUSING: 1 file changed on BOTH sides -- a one-way apply would discard one side's work
    CLAUDE.md
    run `ccs merge` for these, or `--force` to overwrite anyway (destructive)
```

`ccs merge` opens the two versions plus a common ancestor in your own diff tool, and installs nothing until the result is checked for content that went missing.

```bash
ccs merge --only dotclaude/CLAUDE.md --preview   # look, decide nothing
ccs merge --only dotclaude/CLAUDE.md             # resolve in your diff tool, save
ccs merge --only dotclaude/CLAUDE.md --accept    # install, originals backed up first
```

Nothing is installed until you pass `--accept`, and nothing is installed at all if validation finds content from either side missing from the result.

"Missing" is judged against the ancestor. A line one side *added* that the result lacks is loss, and the merge is refused. A line that was in the ancestor and that the *other* side deleted since is a deletion the merge honours -- the payload retired a rule, or this box dropped a section it never wanted -- and the install prints what it honoured, per side, with the first line of each region:

```
merged and installed: CLAUDE.md
    retired upstream (theirs deleted since base): 48 line(s) in 4 region(s)
      - ## Postmortem Commands
      - ### File Editing Workflow
    retired here (you deleted since base): 25 line(s) in 7 region(s)
      - ## Windows Development Environment
```

Read that list. On a right base it is a receipt; on a wrong one it is the alarm -- if a heading you wrote on this box appears under "retired upstream", the merge ran against an ancestor that never held it, and the result should not be accepted.

With no ancestor at all the tool cannot tell a deliberate deletion from an accident, but the person who just resolved the file can: if the only problem is dropped lines and you resolved the file yourself, `merge` prints those lines and asks `install it anyway? [y/N]`. Unattended runs never say yes.

### Adopting a box that forked before the payload existed

A machine whose `CLAUDE.md` split off a year ago has no ancestor in the checkout's history, and a two-way merge of two 1000-line files is a bad afternoon. The ancestor usually exists somewhere -- the home repo of the machine that seeded the box, say. Hand it over and check what the merge would do with it first:

```bash
ccs merge --only dotclaude/CLAUDE.md --base-from C:/Users/me:.claude/CLAUDE.md --dry-run   # nothing written
# would merge: CLAUDE.md
#  #  origin    base              d(ours) d(theirs)  phantom  hunks | ours: silent retired lost | theirs: silent ours-del lost | verdict
#  1  supplied  52768c4:.claude/..    143       794  exempt     24 |     15      15    0 |     29       29    0 | USABLE  conflict-on-delete on
#  2  inferred  (none)  nearest 8d21783 rejected ...                                                           | NO BASE  rule 4: ...
# base: use 52768c4:.claude/CLAUDE.md -- 24 hunk(s) to review; 0 line(s) lost; 15 line(s) of yours retired upstream (theirs wins)
```

`lost` is the number to read: a side's own additions missing from both the clean output and every hunk. It must be 0. `retired` / `ours-del` are deletions the other side made on purpose since the ancestor; the hunks are where you decide about each one -- a supplied base is merged **conflict-on-delete**, so a region the payload removed that this box still carries is shown as a hunk with an empty theirs pane rather than dropped.

```bash
ccs merge --only dotclaude/CLAUDE.md --base-from C:/Users/me:.claude/CLAUDE.md           # resolve the 24 hunks in your tool
ccs merge --only dotclaude/CLAUDE.md --base-from C:/Users/me:.claude/CLAUDE.md --accept  # live only; the checkout stays at HEAD
#   adoption merge: checkout left at HEAD; record 52768c4:.claude/CLAUDE.md as this box's base for CLAUDE.md
```

One base is one file's ancestor, so the run must be scoped to one file with `--only`. `merge.md` walks the whole thing through, including where the ancestor comes from.

`--only` takes an entry (`dotclaude/skills`), a parent of entries (`dotclaude`), or a subtree or file inside an entry (`dotclaude/skills/test-mutation`, `dotclaude/skills/x/SKILL.md`), matched on whole path components -- and it scopes everything: the copy, the two-sided refusal, `--force`, and the direction skips.

## Checking that a step did what it said

```bash
ccs diff                        # which files differ
ccs diff CLAUDE.md              # the actual lines, live vs checkout
ccs diff CLAUDE.md --difftool   # the same, in your own diff tool
ccs diff CLAUDE.md --difftool 3 # live | the commit ccs treats as the common ancestor | checkout
```

`ccs diff <path>` distinguishes three states that are easy to confuse: the file differs (exit 1), the file is `identical -- live and the checkout agree` (exit 0), and no such file (exit 2). After a merge the middle one is what you want to see.

`--difftool 3` opens three panes in your merge tool, read-only: the middle pane is the commit `status` named as the ancestor, so you can check the attribution by eye -- on a `checkout ahead` file the middle pane equals the left one; on `live ahead` it equals the right one; on a two-sided file it equals neither. The output pane is a scratch copy and nothing is written back. If no ancestor can be found, it says why and opens the two-way view instead.

## What the loop looks like when it is done

```
ccs status
  status: clean -- your live config and the checkout match;
          nothing to collect, nothing to apply
```

At that point the only thing left is `git push`, and the checkout is the same on every machine that pulls it.

## Two things the loop does NOT do yet

- **Settings are not composed.** `settings/settings.base.json` uses the `render` strategy -- base plus OS overlay plus machine overlay, with `{{NODE}}` / `{{USER_CLAUDE}}` substituted -- and `render` is Phase 2. `apply` reports it as skipped rather than pretending. This matters for the statusline and hook scripts: `apply` installs them into `~/claude/scripts/`, but the `settings.json` blocks that *point* at them arrive with `render`. Until then those scripts sit inert.

  The ordering is deliberate and worth keeping if you wire them by hand: **scripts first, settings second.** Settings-first wires hooks to scripts that do not exist, and every Bash call then fails noisily.

- **Plugins are not installed.** `settings/plugins.json` uses the `plugins` strategy, also Phase 2, also reported as skipped.

## Safety properties worth knowing

| | |
|---|---|
| `apply` backs up every file it overwrites | before writing, into `~/claude/backups/ccs/` |
| `merge --accept` backs up **both** sides | before writing either, into `~/claude/backups/ccs-merge/` |
| Nothing is ever deleted in place | removals are staged into the backup dir with `--sync-removals`, or only reported |
| The manifest is an allowlist | a file not listed never moves, in either direction |
| Credentials are refused on the way in | `.credentials.json`, `.claude.json`, `settings.local.json`, `*.db`, `history.jsonl` and credential-shaped content, regardless of what the manifest says |
| A file only in your live config is left alone | reported as `local only`, never as a pending removal |
| A file tagged for one box never spreads | an entry with `tags` applies -- and collects -- only on a box whose `~/claude/ccs-box.json` declares every tag; no box file means the entry is off |
