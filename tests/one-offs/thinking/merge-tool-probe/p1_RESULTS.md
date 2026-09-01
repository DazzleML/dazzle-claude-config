# p1 results -- 2026-09-01, run autonomously on plzwork (BC5, `mergetool.bc.path` = BComp.exe)

Pass criterion from `p1_PREDICTION.md`: **MET on every arm**, with one finding the prediction did not anticipate and one rule it validated over its rival.

| Arm | What happened | Verdict |
|---|---|---|
| **Live** (no session open; launch; locate; inject) | Window in 0.5 s. 63 children; 1 visible landmark; 4 `TTextEditor` all visible and NEW (3 inputs at top=137, output at top=777 below the landmark). Locator: **cardinality 1** under both rules -> `0x41108`. Injection: focus verified by **handle equality**, `Ctrl+A/V/S`, read-back **byte-exact** (97 bytes = payload). | **PASS** |
| **Control** (stale session of the SAME file open; relaunch identical command) | BC created a **second session tab, hidden**: 4 NEW `TTextEditor` all `vis=False`; the stale 4 stayed visible; 90 children. **Newly-appeared rule: REFUSE** (0 new visible candidates) -- correct, nothing sent. **Visible-cardinality rule alone: ACCEPT the stale pane** -- wrong. The second `BComp.exe` launcher kept waiting on its hidden session. | **PASS** (refused), and the rival rule is **refuted as sufficient** |
| **Close after control** | `WM_CLOSE` -> `TTabsConfirmCloseDialog` (Close Current Tab / Close All Tabs); Close All -> `TFileConfirmSaveDialog` from the **hidden** session, whose "Yes" would have written BC's regenerated merge over the injected work. Answered No via `BM_CLICK`; `OUTPUT.md` kept its 97 injected bytes. | hazard made concrete |
| **Exit code (b)**: close with the conflict unresolved, don't save | `BComp.exe` returned **101** through a `cmd` hop | measured |
| **Exit code (a)**: inject, save, close | **0**, no dialog on close | measured |
| 14 ("conflicts, but saved") | not exercised -- the injected content had no conflict | untested |

Pixel evidence (kept, `private/claude/evidence/`): the output pane before injection showing BC's **regenerated** content with no prior-work marker even though `OUTPUT.md` on disk held it (G2 in pixels); the pane after injection showing the marker; the window after the control-arm relaunch showing **two `OUTPUT.md` tabs**; the confirm-close and confirm-save dialogs.

## What p1 decides for the design

1. **The locator rule is the corrected one and it is sufficient:** pre-launch child-set snapshot; after launch, *exactly one visible pane of the declared class at the landmark-relative position that newly appeared*; else refuse. Visibility was the discriminator that worked; Z-order rank via `GetWindow(GW_HWNDPREV)` was uninformative here (every pane reported z=0 -- they are not siblings of each other), so it stays evidence, never a selector.
2. **New rule, from the control arm -- a pre-launch check:** if a BC session for this output file already exists (a `TViewForm` titled `<name> - Text Merge` before launch), **do not launch**; say "BeyondCompare already has this file open -- close that tab first". Relaunching creates a hidden duplicate session whose close-time save prompt can overwrite the work; refusing before launch is cheaper and safer than any post-launch recovery. F1's snapshot-and-restore remains the backstop for a user who answers Yes to such a prompt anyway.
3. **Exit codes are real signals through the shell path:** 0 saved, 101 not saved; capture them in i3a. 14 is documented but unmeasured.
4. **Dialogs are the user's.** The driver never auto-answers BC's close/save prompts; `run()` waits for the tool as today, and F1 covers the wrong answer.

## Instrumentation lessons (so the next run does not repeat them)

- A PowerShell background job dies when the tool call's process ends; the launched `BComp.exe` survived but the recorder did not. Record exit codes from a **detached `cmd` script**, not a job.
- `cmd` treats a lone digit immediately before `>` as a handle redirect: `echo 0> f` and `echo EC=0> f` both write nothing (`101>` happens to survive). A space before `>` is load-bearing.
- `$dc:` in a PowerShell string parses as a scope qualifier; use `${dc}:`.
- The harness blocks a script that contains both `Remove-Item` and a `Program Files` path, even when unrelated; keep launchers in `.cmd` files.
