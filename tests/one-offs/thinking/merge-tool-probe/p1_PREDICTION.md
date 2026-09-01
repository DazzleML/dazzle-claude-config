# p1: can the right output pane be located AFTER launch, and is a stale one refused?

Pre-registered 2026-09-01, before any run. Ordered first by the adversarial
review of the injection-driver design (findings 2, 3, 7, 10): identification
after launch is the only unproven step in the whole design, and the schema's
`pane`/`landmark`/`relation` fields are provisional until this settles.

## Step 0 (maintainer, in their REAL terminal -- not the session's shell)

```
python -c "import sys; from dazzle_claude_config import merge; print('interactive:', merge.interactive(), ' stdin-console:', merge._console_attached(sys.stdin), ' stdout-console:', merge._console_attached(sys.stdout))"
```

**Answered by history, no run needed (2026-09-01):** `launch()`'s
`interactive()` refusal and `_console_attached`'s `GetConsoleMode` check both
date from v0.3.0 (`1fcb83a`, 2026-07-28), `interactive()` returns exactly
`_console_attached(sys.stdout)`, and the maintainer's terminal (Windows
Terminal, `CASCADIA_HOSTING_WINDOW_CLASS` per the probe) launched BC on dozens
of `ccs merge` runs on 2026-08-30/31 -- which the refusal makes impossible if
the check were False there. The reviewer's `False` was measured in the agent's
own piped shell, not the maintainer's terminal. Residual, out of scope: a
mintty/MSYS-pty user could still hit the refusal -- a general `ccs merge`
question, not this feature's, and not blocking.

## The instrument

`p1_locate.ps1` (read-only; probes, never focuses or sends):
`-Snapshot <file.json>` records every child of the BC window with hwnd, class,
visibility, Z-order rank (walked via `GetWindow(GW_HWNDPREV)`), geometry, and
landmark-relative position. `-Compare <before.json>` diffs the current tree
against a snapshot and prints the **candidates**: visible panes of the declared
class at the landmark-relative position, marked NEW (absent from the snapshot)
or EXISTING -- and the cardinality verdict.

## Arms

- **Control arm (designed to be refused):** BC already shows a *stale session
  of the same file* (a previous `ccs merge` run left it open). Snapshot ->
  launch the same file again through the same command `launch()` would use ->
  compare. Prediction P-c: BC (single-instance) re-activates the existing
  session; **zero NEW candidates** appear. Under the design's "newly appeared"
  rule the locator must **refuse**. Secondary observation recorded: whether the
  re-activated session's pane is the single VISIBLE candidate (which would make
  "exactly one visible at the landmark" a sufficient rule on its own, and
  "newly appeared" only a stricter one).
- **Live arm:** no session of the file open. Snapshot -> launch a resumed file
  -> compare. Prediction P-l: **exactly one NEW visible** candidate; dead panes
  from earlier sessions, if any, are `vis=False` and excluded. Then
  `bc_inject.ps1` with that handle -> read back the saved file -> hash equals
  the injected content.
- **Exit-code hop:** launch through `cmd /c` (mirroring `launch()`'s
  `shell=True`) and record the code observed on (a) save-and-close, (b) close
  with conflicts unresolved. Prediction: 0 and 14/101 survive the hop.

## Pass criterion (written before running)

- Live arm: cardinality 1, NEW, visible; injection verified by read-back hash.
- Control arm: cardinality of NEW candidates is 0 -> refusal, **no keystroke
  sent**.
- Both: every `vis=False` pane is excluded from candidates.

Any other outcome is recorded as a refutation of the corresponding rule and the
schema is redesigned before i1 is written. Pixel evidence via `/screenshot`
(the pane before and after injection) is kept in `private/claude/evidence/`
under the provenance naming; nothing else is kept.
