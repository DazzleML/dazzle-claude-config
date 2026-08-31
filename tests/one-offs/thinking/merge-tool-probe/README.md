# Probing a GUI merge tool from outside the process

Three scripts, kept because the technique is reusable and because one of them is
the only reason a wrong answer was caught. The capture that caught it is not
here -- screenshots are project-private; see "Why the screenshot script exists".

They belong to the design in
`2026-08-31__13-42-07__dev-workflow-process__handing-a-partial-merge-back-to-a-tool.md`
(project-private) and will be needed by its units u5-u7 -- the tool-capability
survey and the injection driver.

## The scripts

| Script | What it does | Safe? |
|---|---|---|
| `bc_probe.ps1` | Enumerates a tool's window tree: class names, control ids, geometry, visibility. Flags which controls are standard text controls. | **Read-only.** Sends nothing |
| `bc_shot.ps1` | Captures one window **or one child control** to PNG | Read-only |
| `bc_inject.ps1` | The injection: focus the output pane, `Ctrl+A`/`Ctrl+V`/`Ctrl+S`, restore the clipboard | **Writes.** Drives a real GUI |

```powershell
.\bc_probe.ps1                              # list candidate tool windows
.\bc_probe.ps1 -Hwnd 0x1434430              # enumerate one window's children
.\bc_shot.ps1  -Hwnd 0xC137F0 -Out pane.png # capture ONE control
.\bc_inject.ps1 -Pane 0xC137F0 -Top 0x1434430 -Save -OutFile out.txt
```

## Why the screenshot script exists, and why it is the important one

`bc_inject.ps1`'s early version checked whether `WM_SETTEXT` had worked by
reading the window text back. **It reported success. It was wrong.**

BeyondCompare's panes are `TTextEditor`, a custom-drawn Delphi control that
renders from its own buffer. `WM_SETTEXT` set the window's *text property*;
`GetWindowTextLengthW` then read that property back; and a check written as
"did the text change?" was reading the thing that had changed rather than the
thing that mattered.

The capture that settled it -- the pane still holding the real merged content
while the assertion claimed otherwise -- is kept project-private, because it
shows the contents of a maintainer's own config file:
`2026-08-31__11-30-00__bc__output-pane__wm_settext-did-nothing.png`.

**The transferable rule: on a custom control, the window text property and the
rendered text are different things, and the obvious check tests the wrong one.**
Anyone reimplementing this will hit it and believe it worked.

Two details make the capture work where a naive one does not:

- `PrintWindow(hwnd, dc, 2)` -- flag 2 is `PW_RENDERFULLCONTENT`, which
  captures composited/DirectComposition surfaces that a plain `BitBlt` of a
  partly-occluded window misses.
- Passing a **child control's** handle, not the top-level window's. That is what
  turns "a screenshot of an app" into "the contents of the pane in question",
  small enough to read directly.

## Where this fits in a test process

None of this can run in CI -- it needs a real GUI and steals focus. The split:

- **Automatable, no GUI** -- everything about the *capability model*: which tier
  a tool is in, whether a resumed file is reopened, what the output says. That
  is u1-u4 of the design and it is ordinary unit testing.
- **Needs a real tool, human present** -- that a given tool actually preserves
  or discards `$MERGED`. This is the calibration probe (`--probe-tool` in the
  design), run once per tool per machine, not per merge.
- **Human checklist** -- that the injection lands in the right control, that
  focus and clipboard are restored, that a failure halfway leaves nothing
  broken.

The general principle these scripts encode: **verify by reading the artifact,
not by trusting the send.** `bc_inject.ps1` only became trustworthy when it
stopped believing its own keystrokes and read the saved file instead.

## Fragility, each item measured

- BC is single-instance: it reuses one window and swaps the session, so the
  title changes underneath you.
- Old session controls survive with `vis=False` and their handles **stay
  valid** -- target one and you drive a dead pane, silently and "successfully".
- Handles change per session; control ids are not stable. Identify by class plus
  geometry relative to a landmark control, never by index.
- `SendKeys` goes to the foreground. It steals focus, and anything the user
  types during the operation lands in the payload.
- The clipboard is global. Saved and restored here, but a user copying
  mid-operation is a real race.
- `bc_inject.ps1` restores the clipboard and **not** the foreground window.
  `c:\code\notepad-cleanup` already does the latter; copy it when this becomes
  real code.
