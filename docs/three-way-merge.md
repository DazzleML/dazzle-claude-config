# A three-way merge, if you have never done one

`ccs merge` will, at some point, open a window with three or four panes and wait for you. If that window is familiar, skip this page. `merge.md` covers what ccs itself does. This page is for the first time.

## The idea in one paragraph

Two copies of a file changed. Looking at just the two of them, a difference is ambiguous: if a paragraph is in yours and not in theirs, either *you added it* or *they deleted it* -- and those call for opposite resolutions. The trick is to bring in a third copy: the **common ancestor**, the file as it was when both sides last agreed. Now every difference has a direction. In yours but not the ancestor: you added it (keep it). In the ancestor but not theirs: they deleted it (a question worth asking, but a deliberate one). Both sides changed the same lines since the ancestor: a genuine conflict, and the only kind a human *must* decide. A good merge tool resolves everything with a direction automatically and asks you only about the conflicts.

Two short videos, if watching beats reading: [what a three-way merge is](https://www.youtube.com/watch?v=Y1oFXujkZJI), and [one resolved in GitKraken](https://www.youtube.com/shorts/JN2U2Qiftpg) (the mechanics are the same in every tool).

## The window

Almost every tool uses the same layout:

```
+----------------+----------------+----------------+
|     OURS       |      BASE      |     THEIRS     |   three inputs, read-only
|  (your copy)   | (the ancestor) | (their copy)   |
+----------------+----------------+----------------+
|                    OUTPUT                        |   the one pane you edit
|        (becomes the merged file on save)         |
+--------------------------------------------------+
```

Beyond Compare and kdiff3 look exactly like this. vimdiff puts the same four panes in a terminal (ours | base | theirs on top, output below). VS Code's merge editor shows ours and theirs on top with the result below, and tucks the ancestor behind a toggle. GitKraken stacks them the same way. The names shift between tools (LOCAL/BASE/REMOTE, "current/incoming", left/centre/right) but the middle (or hidden) one is always the ancestor, and there is always exactly one pane whose content becomes the saved file. **Find that pane first.** Everything else is input.

ccs labels the sides the way git does: **ours** = your live file, **theirs** = what the other machines pushed, **base** = the ancestor.

## The working rhythm

The tool has already resolved everything it could. What remains is a list of conflict regions, and every tool gives you the same four moves:

1. **Next conflict** (a button or key, `n` in some tools) -- jump to the first undecided region.
2. **Take one side** -- a click on the ours or theirs pane's arrow (Beyond Compare), `:diffget` from a pane (vimdiff), "Accept current/incoming" (VS Code). The region lands in the output pane.
3. **Take both, or edit** -- when the sides did different good things, take one, then type the rest directly into the output pane. It is an ordinary editor; nothing stops you from writing the sentence that combines them.
4. **Next conflict** again, until the counter reads zero. Save. Quit.

That is the entire process. Three habits make it comfortable:

- **Read the base pane before deciding.** It tells you what the region *used to* say, which is what turns "these two differ" into "they rewrote this rule" or "I never had this".
- **Decide the file's owner per region, not per file.** A config file is a list of independent rules; taking "all ours" or "all theirs" wholesale is almost always wrong somewhere.
- **When unsure, keep both and mark it.** Put both versions in the output with a `TODO` line above them and come back after. A kept-twice rule is a visible nuisance; a dropped rule is invisible.

## What it looks like in the tools people actually use

**Beyond Compare** (three panes up top, output below): conflicts are tinted; the small arrows at each pane's edge push that pane's region into the output; the output pane is directly editable; `Ctrl+S` saves, and closing the window returns to ccs. Sections view -> "Show conflicts" filters to what needs you.

**vimdiff / nvim** (`ccs merge --tool vimdiff`, no configuration needed): four panes, cursor starts in the output (bottom). `]c` / `[c` move between regions; `:diffget LO`, `:diffget BA`, `:diffget RE` pull from ours/base/theirs; edit freely; `:wqa` saves and exits. It looks austere and works everywhere, which is why servers use it.

**VS Code**: `git config --global mergetool.code.cmd 'code --wait --merge $REMOTE $LOCAL $BASE $MERGED'` once, then `ccs merge --tool code`. The checkboxes above each conflict accept a side; the result pane at the bottom is editable.

**[WinMerge](https://winmerge.org/)** (free, open source, Windows): does real three-way merges -- ours | base | theirs up top, and the merge result assembled as you click regions into place. One line wires it to ccs (adjust the install path):

```
git config --global mergetool.winmerge.cmd '"C:/Program Files/WinMerge/WinMergeU.exe" -u -e -wm -dl ours -dm base -dr theirs "$LOCAL" "$BASE" "$REMOTE" -o "$MERGED"'
```

then `ccs merge --tool winmerge`. A good first tool on Windows if you do not own Beyond Compare.

**GitKraken, kdiff3, meld**: same steps, different styling. kdiff3 and meld need no configuration with ccs; GitKraken takes one `mergetool.<name>.cmd` line.

## The two ccs-specific things to know

**A hunk with an empty theirs pane** (`<<<<<<< ours (kept verbatim; theirs deleted it since base)`) appears when you supplied an ancestor for an adoption merge: the other machines removed this region since the ancestor, and this box kept it. Take ours to keep your section; take theirs (empty) to retire it. This is a real decision about *your* content -- it is the reason the merge opened at all -- so read these ones properly.

**Saving does not install.** The result waits in `~/claude/merge/ccs/` and is checked (nothing either side held may go missing without cause) before `--accept` writes anything. You can close the tool, re-run `ccs merge`, and your half-finished resolution is still there. There is no state to be afraid of breaking: until `--accept`, nothing you do in the tool touches your live config or the checkout, and even `--accept` backs up both originals first.

## See also

- `merge.md` -- what ccs does around the tool: the dry-run table, the receipt, adoption, refusals
- `ccs merge -h` -- the commands
