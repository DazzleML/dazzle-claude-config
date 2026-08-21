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

# 1. get what other machines sent
git -C "$CCS_CHECKOUT_DIR" pull

# 2. look before you leap -- nothing is touched
ccs status

# 3. reconcile anything that changed on BOTH sides (see below)
ccs merge

# 4. bring the rest in
ccs apply

#    ... work. edit skills, commands, CLAUDE.md, whatever ...

# 5. send your changes out
ccs collect

# 6. share them
git -C "$CCS_CHECKOUT_DIR" add -A
git -C "$CCS_CHECKOUT_DIR" commit -m "config: ..."
git -C "$CCS_CHECKOUT_DIR" push
```

Steps 1 and 6 are plain git. ccs does not wrap them, because git is already good at them and hiding them would obscure where your config actually lives.

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
