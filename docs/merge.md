# Merging: when a file changed on both machines

`collect` and `apply` are one-way copies. They are the right tool for the common case (i.e. you edited a skill here, or another machine pushed a new one) and `collect`/`apply` are the wrong tool the moment the *same file* changed on both sides (exactly because a copy in either direction discards whatever the losing side added, silently, with a success message). `ccs status` tells the two cases apart and marks the files that need `merge`; and this document is about how to handle merging.

It is written for the person at the keyboard, not for the code. If you only need the commands, `ccs merge -h` has them. If you have never resolved a three-way merge in a visual tool, read `three-way-merge.md` first -- ten minutes there makes everything below routine.

## The three sides

Every merge has three inputs, and the words ccs uses to describe them are git's terminology:

| word | what it is | where it lives |
|---|---|---|
| **ours** | your live file | `~/.claude/...` or `~/claude/...` |
| **theirs** | the checkout's committed copy -- what the other machines pushed | the payload repo at `HEAD` |
| **base** | the common ancestor: the file as it was the last time both sides agreed | inferred from the checkout's history, or supplied by you |

The base is what makes a merge a merge rather than a guess. With it, a line that is in the base and in ours but not in theirs means *they deleted it*; a line in theirs but not in the base means *they added it*. Without it, the tool can only see that two files differ, and every difference is a question for you.

ccs infers the base by asking the checkout's history which commit each side still equals -- `ccs status --long` shows the evidence (`both moved since 4c2935a`). When nothing in history attributes the change, it says so (`no ancestor in history attributes this change`) and merges two-way, honestly, rather than inventing a third input.

## The usual loop

```bash
ccs status                                # marks the files that changed on both sides
ccs merge --dry-run                       # what would merge, and which ancestor each file would use
ccs merge --preview                       # the three sides in your diff tool; nothing decided
ccs merge                                 # resolve; the result waits in the workspace
ccs merge --accept                        # install on both sides, originals backed up first
```

`merge` opens your own tool -- whatever `git mergetool --tool-help` lists: Beyond Compare, kdiff3, meld, WinMerge, vimdiff, VS Code -- with ours on the left, theirs on the right, the base in the middle, and the output pane at the bottom. Resolve, save, quit. The result sits in `~/claude/merge/ccs/` until you pass `--accept`; re-running `merge` picks up your edits rather than re-seeding, so you can stop and come back.

Nothing is installed until two things are true: you passed `--accept`, **and** the result passed validation -- the check that nothing either side held went missing. That check is the point of the whole design; the refusals it produces are listed at the end of this document.

## Reading `--dry-run`

```
would merge: CLAUDE.md
 #  origin    base                          d(ours) d(theirs)  phantom  hunks | ours: silent retired lost | theirs: silent ours-del lost | verdict
 1  inferred  4c2935a  checkout history         12        40  0.00/0      2 |      0       0    0 |      3        3    0 | USABLE  (history)
  base: use 4c2935a -- 2 hunk(s) to review; 0 line(s) lost; 0 line(s) of yours retired upstream (theirs wins)
        3 theirs line(s) stay deleted (you removed them since base): first '## Old section'
```

One row per candidate ancestor. Reading left to right:

- **d(ours) / d(theirs)** -- how many lines each side changed since that ancestor. A good ancestor is close to both.
- **phantom** -- the sibling check: of the lines this ancestor says *you* deleted, the fraction the other side still has. A high ratio over three or more lines means the "ancestor" is really a sibling of theirs that you never descended from, and it is rejected (`NO BASE  rule 4`). A supplied ancestor is `exempt` -- you told the tool it is a fact, and the table is the judgement instead.
- **hunks** -- how many conflicts you would be asked to resolve.
- **silent** -- lines unique to that side that are absent from both the clean output *and* every hunk: dropped without anyone being asked.
- **retired / ours-del** -- of the silent lines, the ones the ancestor also holds. They went silent because the *other* side deleted them since the ancestor: a deliberate deletion the merge honours. `retired` (ours column) = upstream retired them; `ours-del` (theirs column) = you removed them.
- **lost** -- of the silent lines, the ones the ancestor does *not* hold: a side's own addition, gone. **This must be 0.** A merge never drops a side's additions; if this is non-zero the tool is wrong, not your ancestor. Say so in an issue.

The `base:` line is the verdict in words, naming the first retired line on each side so you can recognise it.

## Reading the receipt

When a merge installs, it prints what it honoured:

```
merged and installed: CLAUDE.md
    retired upstream (theirs deleted since base): 48 line(s) in 4 region(s)
      - ## Postmortem Commands
      - ### File Editing Workflow
    retired here (you deleted since base): 25 line(s) in 7 region(s)
      - ## Windows Development Environment
```

Read it. On a right ancestor this is a receipt: the other machines retired those sections, and your copy now agrees. On a wrong ancestor it is the alarm -- **a heading you wrote on this machine appearing under "retired upstream" means the merge ran against an ancestor that never held it**, and the result should not have been accepted. (Validation cannot catch this case: as far as the files are concerned, the deletion was legitimate. Only you know which sections are yours.)

## When there is no ancestor

Two-way merge: your tool opens with an empty middle pane, the output pane seeded with ours, and every difference a question. Resolve as usual. One thing changes at validation time: with no ancestor the tool cannot tell a deliberate deletion from an accident, so if the only problem with your result is that lines one side held are missing, and you resolved the file yourself, it shows you those lines and asks:

```
no base for CLAUDE.md: ccs cannot tell whether these lines were deleted on purpose or by accident --
  only in theirs, absent from your result (2):
    - the line you chose to drop
    - and another
  you reviewed this file -- install it anyway? [y/N]
```

Answer `y` only if you meant it. Unattended runs (no console, CI) never say yes. A fresh, untouched seed is never asked about -- the prompt is for *your* result.

## Adopting a machine that forked before the payload existed

This is the hard case, and the reason the machinery above exists. A machine set up a year ago from another machine's config, that has been editing its own `CLAUDE.md` ever since, has no ancestor in the payload's history -- the payload did not exist when the two copies parted. A two-way merge of two thousand-line files is a bad afternoon, and the nearest commit in the payload is a *sibling*, not an ancestor: merging against it invents deletions for everything the box wrote (measured once: 296 lines silently gone).

The ancestor does exist, though. It is the file as it was when the box was first set up, and it is almost always in the git history of the machine that did the seeding.

### Finding the ancestor

```bash
git -C <repo> log --oneline --date=short --format="%h %ad %s" -- .claude/CLAUDE.md
```

where `<repo>` is the home repo of the seeding machine (many people track `~/.claude` in one), a backup repo, or the payload itself if it is old enough, and `.claude/CLAUDE.md` is the file's path *inside that repo*. Pick the commit from around the day the box was set up -- the most recent one **before** the two copies diverged. Eyeball it:

```bash
git -C <repo> show <sha>:.claude/CLAUDE.md | head -40
```

It should read like both copies' common past. If you have the old file on disk instead (a backup), `--base-file FILE` takes it directly.

### Check it before you merge

```bash
ccs merge --only dotclaude/CLAUDE.md --base-from <repo>@<sha>:.claude/CLAUDE.md --dry-run
```

(`--only` is required: one ancestor is one file's. On Windows the repo path may carry a drive letter -- `C:\Users\me@52768c42:.claude/CLAUDE.md` parses; the path is whatever follows the last colon.)

Two things tell you whether you picked the right revision: `lost` is 0, and the first lines named under `retired upstream` and `ours-del` are sections you recognise as the *other* platform's, or as genuinely retired. If a heading this box wrote shows up as "retired upstream", the ancestor never held it -- wrong revision; try an earlier one. If the table says `NO BASE` for the inferred row, that is expected: it is telling you the payload's history has nothing usable, which is why you are supplying one.

### Why the merge looks different with a supplied ancestor

A correct three-way merge against a true ancestor has a side effect nobody wants on a box with its own manual: every region the payload removed since the ancestor, that this box left untouched, is *honoured* -- deleted from the result without a word. On the file this was built against, 166 lines in 38 headings vanished that way. So a supplied ancestor is merged **conflict-on-delete**: each such region becomes a hunk.

In your tool that hunk looks like this -- the region on the left (ours) and in the middle (base), and **nothing on the right** (theirs):

```
<<<<<<< ours (kept verbatim; theirs deleted it since base)
## Port allocation on this box
- 8080 api
- 8443 tls termination
||||||| base
## Port allocation on this box
- 8080 api
- 8443 tls termination
=======
>>>>>>> theirs
```

Take the left to keep the section; take the right to let it go. Where the payload *replaced* a region rather than deleting it, the right pane holds the replacement and the middle pane still shows the region (ccs restores it there so the three panes tell the truth -- a replacement, not two additions). `--block-swap-ratio` tunes how different a replacement must be before it counts as a removal; the default 0.6 sat on a plateau from 0.45 to 0.70 on the real file, so leave it unless the hunk count looks wrong.

166 silent lines became 24 hunks that way, with `lost` 0.

### Installing

```bash
ccs merge --only dotclaude/CLAUDE.md --base-from <repo>@<sha>:.claude/CLAUDE.md --accept
#   merged and installed LIVE ONLY: CLAUDE.md
#   adoption merge: checkout left at HEAD; record 52768c42:.claude/CLAUDE.md as this box's base for CLAUDE.md
```

`--accept` on a supplied ancestor installs the **live file only**. The checkout stays at HEAD on purpose: installing this box's own sections into the shared payload would publish them to every other machine and make them the next inferred ancestor -- which is exactly the mechanism that deletes them on the following merge. Write down the ancestor it names; the next merge of this file wants the same one (or the payload commit you just merged, never the merged output).

The longer-term fix for a box with its own sections is not a better merge but a better layout: its sections in a file of their own that the shared `CLAUDE.md` imports, so the shared file is one-sided from then on. See `sync-loop.md`.

## A box with no diff tool

Servers rarely have Beyond Compare. Three options, in the order to try them:

1. **Do the merge from a machine that has the tool, against the box's live tree.** If the box's home is mounted (sshfs, a network drive), point `--claude-dir` and `--user-claude` at the mount and run the merge from your desk. The result installs onto the box; the backups land in the box's own `~/claude/backups/`. One check before `--accept`: a Windows tool may write CRLF line endings into the workspace file -- ccs restores the live file's own endings on install, but look at the workspace copy (`~/claude/merge/ccs/*.merged` on the mount) if the box is Linux and your tool is not.
2. **A terminal merge tool on the box.** `ccs merge --tool vimdiff` (or `nvimdiff`) needs no git configuration: it opens git's four-pane layout -- LOCAL | BASE | REMOTE on top, MERGED below -- where `:diffget LO`, `:diffget BA`, `:diffget RE` pull a hunk from a pane and `:wqa` finishes. `meld` and `kdiff3` are built in the same way. Steeper than a GUI, but it is the standard server practice, and with nothing configured ccs picks whichever of them is installed.
3. **No tool at all.** `ccs merge --no-launch` seeds the workspace file with the conflict markers and stops; edit it by hand (`<<<<<<<` / `|||||||` / `=======` / `>>>>>>>`), then re-run `merge` -- it keeps your edits -- and `--accept`.

## `--union`

`--union` keeps *both* sides of every conflicting region instead of asking: right when each side added different paragraphs, wrong when they edited the same sentence (you get both versions). Validation still runs and catches the duplication it tends to cause. It is refused with a supplied ancestor: an adoption merge is a review by definition, and `--union` is the opposite of one.

## When `merge` refuses

| message | meaning | what to do |
|---|---|---|
| `unresolved conflict markers` | you saved the file with `<<<<<<<` still in it | open it again and finish |
| `N line(s) present only in ours/theirs are missing from the result` | a line one side *added* is gone from your result, not replaced by a rewrite | put it back, or -- no base only -- answer the prompt |
| `N line(s) in the result appear in neither side nor the base` | invented content: the tool is not a place to write new text | take it out; edit the live file after the merge |
| `content was duplicated` | the same substantial line landed twice (usually `--union`) | remove one |
| `content lost: 'name' was present on an input side` | a named probe (something the manifest or a hint asked to preserve) is gone | put it back |
| `--base-file/--base-from is one file's ancestor, but this run covers N` | `--only` did not narrow the run to one file | add `--only <entry/path>` |
| `--union keeps both sides without review` | `--union` with a supplied ancestor | drop `--union` |
| `no console attached -- refusing to launch` | CI or a piped shell | `--no-launch`, or a real terminal |

Exit codes: 0 resolved (or installed), 1 something was refused, 2 an error, 4 a result failed validation and was not installed.

## See also

- `three-way-merge.md` -- the merge window itself, for the first time
- `sync-loop.md` -- the whole loop, and why `status` fetches first
- `ccs merge -h` -- the commands, short form
- `tests/checklists/v0.4.3__Feature__adoption-merge-supplied-base-and-honoured-deletions.md` -- the human test checklist, with a scratch world you can practise on
