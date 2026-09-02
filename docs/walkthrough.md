# ccs, walkthrough end to end

The README's Quick Start gets one machine syncing in five commands. This page is the long version: a first machine from nothing, the choices you make on the way, a second machine, and a box that forked before the payload existed -- with what each step guarantees and how to check it did what it said. It is written so a person can also run it as a test: every step names an observable outcome.

If you have never seen the tool: **your live config is what Claude Code reads (`~/.claude`, plus `~/claude` for files you own); the payload checkout is a git clone that Claude Code never looks at.** ccs moves files between the two, guarded and backed up. Git moves the checkout between machines. That is the whole shape; [the sync loop](sync-loop.md) is its one-page form.

## 0. What you need

- Python 3.10+ and git. ccs itself has no other dependency, on purpose -- a first `pip install` on a bare VPS must fail for exactly one reason if it fails at all.
- A payload repo to point at. Three cases, in section 2.
- A diff/merge tool if you want `ccs merge` to open one (BeyondCompare, vimdiff, kdiff3, meld ...); without one, `--no-launch` leaves conflict markers in a file you edit by hand.

## 1. Install, then let the tool tell you what is missing

```bash
pip install dazzle-claude-config     # installs `ccs`
ccs --version
ccs doctor                           # read-only: interpreter, git, checkout, remote, box identity, config, manifest, seeds
```

**Expect:** `doctor` changes nothing and pairs every finding with the command that fixes it. On a fresh machine it will say there is no checkout and no box declaration yet -- that is the state, not a fault.

Give the machine a name and tags. Tags are names you choose (not hostnames); they decide which payload entries apply here.

```bash
ccs setup box --name laptop --tag laptop --tag dev     # writes ~/claude/ccs-box.json; never overwrites an existing one
ccs setup                                              # bare: the check-up -- what is configured, what is not
```

**Expect:** `~/claude/ccs-box.json` holds `{"name": "laptop", "tags": ["laptop", "dev"]}`. Running `setup box` again refuses to overwrite it.

**Why it matters:** an entry with `tags` applies -- and collects -- only on a box that declares every tag it names. No box file means no tags, which switches every tagged entry *off*. Declaring identity first means the first `apply` delivers the right machine-specific files and none of the wrong ones.

## 2. Point ccs at a payload

A payload is any git repo that holds config: skills, commands, agents, hooks, a `CLAUDE.md`, optionally a `ccs-manifest.json` that says what goes where. Pick the case that is yours.

**a. Try a public collection, read-only.** Nothing you do here touches the collection; you can borrow a slice.

```bash
git clone https://github.com/DazzleML/dazzle-claude-code-config ~/claude/dazzle-config
ccs status --checkout-dir ~/claude/dazzle-config
```

**b. Your own payload repo** (the main case: your config follows you between machines).

```bash
git clone <your-payload-repo> ~/claude/my-config
```

**c. You have a `~/.claude` full of work and no repo yet.** Package it -- the empty directories are how you say *what* to track:

```bash
gh repo create my-claude-config --private
git clone <that repo> ~/claude/my-config
cd ~/claude/my-config && mkdir skills commands agents && touch CLAUDE.md
ccs collect --checkout-dir ~/claude/my-config      # guarded: deny-list + credential scan on the way in; refusals are reported, never silent
git add -A && git commit -m "my config" && git push
```

Then stop typing the flag:

```bash
export CCS_CHECKOUT_DIR=~/claude/my-config        # POSIX; add to your shell profile
```
```cmd
setx CCS_CHECKOUT_DIR C:\src\my-config             # Windows; applies to new shells
```

**Expect:** `ccs doctor` now reports the checkout, its remote, and the manifest it found (or the implicit layout it inferred from a repo that looks like a `~/.claude`).

## 3. Look before anything is touched

```bash
ccs status          # three-way drift: live vs checkout, checkout vs remote, uncommitted work in the checkout
ccs diff            # which files differ, per entry
ccs diff CLAUDE.md  # the actual lines
```

**Expect:** on a first contact, most entries show as "checkout ahead" (you have nothing yet) and the summary is not `clean`. Nothing has been written. `status --long` says which side owns each change and how sure it is; `unattributed` means it will not guess, and names `ccs diff` instead of a verb that writes.

## 4. The first apply -- what actually happens

```bash
ccs apply --dry-run     # every action it would take, and none taken
ccs apply               # checkout INTO live; every overwritten file backed up first
```

What lands, by strategy (the manifest decides; a payload without one is treated as a `~/.claude` layout):

| Strategy | On first apply | Afterwards |
|---|---|---|
| `copy` | installed | kept in sync both ways; a file changed on **both** sides is refused, never overwritten -- that is what `merge` is for |
| `seed-if-absent` | delivered **only if you have no such file** | never touched again -- it is yours; `status` does not count your edits as drift |
| entries with `tags` / `os` | installed only where the box declares every tag / matches the OS | same gate on `collect`, so a box without the tag cannot stage the file into the shared checkout by accident |
| `render`, `plugins` | reported as skipped (not built yet) | -- |

**Expect:** a line per file (`applied`, `seeded`, `skipped ... live is ahead`, `REFUSING ... changed on BOTH sides`), a backup directory named once in the summary, and `ccs status` afterwards reading `clean` except for seeded files, which it lists as yours and uncompared.

**Guarantees worth knowing now:** nothing is ever deleted in place -- a removal is a move into `~/claude/backups/ccs/`; a file only in your live config is reported as `local only`, never as a pending removal; the home directory being a git repo makes ccs refuse to operate on it at all.

## 5. The `CLAUDE.md` choice -- and why nothing forces it

Both the public collection and the layered personal payloads deliver `CLAUDE.md` as **`seed-if-absent`**. If you already have one, **it is kept, byte for byte**, and the first apply does not change it. A plain, monolithic `CLAUDE.md` of your own is a fully supported state -- there is no template you must follow.

What you are choosing between:

- **Keep yours.** Nothing to do. Layer files the payload ships (`~/claude/claude-config/global.md` and friends, in a layered payload) may still arrive as ordinary entries, but nothing reads them until a `CLAUDE.md` imports them. `ccs status` will keep reporting your `CLAUDE.md` as a seeded file that differs from the payload's seed -- that is a question, not a defect. Answer it once: `ccs seed keep CLAUDE.md --always` (or `--until-changed`, asked again only if the payload's seed changes).
- **Take the payload's.** `ccs seed migrate CLAUDE.md` does the move and proves it: your file is hashed, a copy is kept outside the backup tree, the payload's version is installed, and both copies are verified against your original bytes. In a layered payload the new `CLAUDE.md` is a short pointer stub that imports `global.md` (the shared rules), `platform.md` (chosen by OS), `machine.md` (this box's own section), `projects.md`, and `task-rules.md`. Your old content does not vanish -- the copy is named in the output -- but nothing moves it into the layers for you yet (that is the adoption case in section 8).
- **Look first.** `ccs diff CLAUDE.md --difftool` opens yours against the payload's in your own tool, read-only. Bare `ccs seed` walks every open seeded-file question one keystroke at a time, with `[d]` for exactly this.

**Expect after either choice:** `ccs status` no longer asks about `CLAUDE.md`, and `ccs seed list` shows the recorded answer.

## 6. Day to day

```bash
ccs git pull        # 1. what other machines sent (status says when this is due)
ccs status          # 2. look
ccs merge           # 3. files that changed on BOTH sides -- your diff tool decides; nothing installs without --accept
ccs apply           # 4. the rest, in
#   ... work ...
ccs collect         # 5. live INTO checkout (credentials refused; a file you edited in the checkout but never committed is refused, not overwritten)
ccs git add -A && ccs git commit -m "config: ..." && ccs git push   # 6. share
```

`ccs git <anything>` is `git -C <checkout>` from wherever you are. `status` fetches before it reports, so `in sync` is a claim about the remote; with `auto_pull: true` in `~/claude/ccs-config.json` it fast-forwards itself, strictly `--ff-only`.

**Expect at the end of a loop:** `status: clean -- everything ccs syncs matches; nothing to collect, nothing to apply`. `clean` covers all three legs: it will not say so while the checkout holds commits the remote lacks.

**Merging, briefly** (the full story is [merge.md](merge.md)): `ccs merge` seeds a workspace file per two-sided entry under `~/claude/merge/ccs/`, opens your tool on ours / base / theirs with that file as the output pane, and validates the result before `--accept` installs it -- content from either side that went missing refuses the install. Stop partway and come back: a file you already resolved is recognised as `resumed`, and what happens next depends on your tool -- vimdiff reopens it with your edits in place; BeyondCompare, which regenerates its output pane, leaves it closed until `--relaunch`, which (on Windows) reopens it and paints your edits back in after asking. `ccs merge --accept --no-launch` installs what you kept without opening anything. The full table is in [merge.md](merge.md), "Stopping partway and coming back".

## 7. A second machine

```bash
pip install dazzle-claude-config
ccs setup box --name desktop --tag desktop --tag dev
git clone <your-payload-repo> ~/claude/my-config && export CCS_CHECKOUT_DIR=~/claude/my-config
ccs status && ccs apply
```

**Expect:** the same shared files as the first machine; the `laptop`-tagged files absent (`status --long` lists what the gate kept off and why); on a layered payload, `platform.md` is the variant for this OS and `machine.md` is either this box's own file (if the payload carries `machines/desktop/`) or the seeded blank template. From here both machines run section 6.

## 8. A box that forked before the payload existed

The hard case: a machine whose `CLAUDE.md` split off a year ago. No commit in the checkout is its ancestor, and a two-way merge of two 1000-line files is a bad afternoon. The ancestor usually exists somewhere else -- the home repo of the machine that seeded this one, an old clone. Hand it over and read what the merge would do with it **before** doing it:

```bash
ccs merge --only dotclaude/CLAUDE.md --base-from <repo>[@<sha>]:<path> --dry-run
```

**Read `lost`; it must be 0.** `retired` / `ours-del` are deletions the other side made on purpose since the ancestor; a supplied base merges **conflict-on-delete**, so every section the payload removed that this box still carries becomes a hunk with an empty theirs pane, for you to decide -- never a silent drop.

```bash
ccs merge --only dotclaude/CLAUDE.md --base-from <repo>@<sha>:<path>            # resolve the hunks in your tool
ccs merge --only dotclaude/CLAUDE.md --base-from <repo>@<sha>:<path> --accept   # installs into LIVE only; the checkout stays at HEAD
```

**Expect:** a receipt naming, per side, the first line of each region it let go. On a right base it is a receipt; on a wrong one it is the alarm -- a heading you wrote on this box under "retired upstream" means the ancestor never held it, and the result should not be accepted. `--accept` on an adoption merge is live-only by design: installing a box's own sections into the shared checkout would publish them to every other machine and make them the next base.

After the merge, the layered choice from section 5 applies (`ccs seed migrate CLAUDE.md` for the stub); distributing the box's own sections into `machine.md` and the layers is, today, editing you do -- the tool keeps the copies and proves nothing was lost, and stops there.

## 9. The public collection and a personal payload are not the same shape

Today the public collection's `CLAUDE.md` is a full file importing three seeded, user-owned files -- `environment.md` (blank on delivery, "edit freely, it is yours"), `projects.md`, `task-rules.md` -- with **no per-OS or per-box selection**: nothing can clobber those files, but nothing delivers platform rules by architecture either; you write them. A layered personal payload delivers `platform.md` by OS and `machine.md` by tag through the same manifest gate section 4 describes. Bringing the public collection onto the layered model is tracked in that repo; until then, know which shape your payload has before reading `status --long`.

## 10. Checking that any step did what it said

```bash
ccs diff                          # which files differ
ccs diff <file>                   # the lines
ccs diff <file> --difftool 3      # live | the commit ccs calls the ancestor | checkout, read-only
ccs status --long                 # per-file: which side moved, and the evidence
ccs doctor                        # everything the environment should have, and the fix for each gap
```

`ccs diff <file>` exits 0 for identical, 1 for differs, 2 for no such file -- after a merge, 0 is what you want to see.

## Using this page as a test

Every **Expect** line is a checkable outcome; the sections are ordered so a fresh machine (or a scratch `--claude-dir` / `--user-claude` / `--checkout-dir` triple, which keeps a run away from your real config) can be walked top to bottom. A run that stops at any Expect line has found something worth filing.
