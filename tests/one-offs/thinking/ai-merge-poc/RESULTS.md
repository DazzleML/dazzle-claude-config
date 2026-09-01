# p0 results -- 2026-09-01, first and only run

Pass criterion from PREDICTION.md: **MET, both halves.**

| Arm | Model output | validate() |
|---|---|---|
| A harmonize (control) | `Files are staged to a recovery folder after an interactive confirmation when the payload retires them.` -- the exact harmonization the review predicted | **FAIL**: "1 line(s) in the result appear in neither side nor the base", plus the duplication counter also fired |
| B line-selection | `Files are staged to a recovery folder when the payload retires them.` (ours' line, verbatim) | **PASS**, zero failures |

Secondary recordings: prompts 748 / 851 bytes for one hunk; `claude -p` round
trips 5.3s / 5.0s; the CLI works from inside a Claude Code session with
`CLAUDECODE`/`CLAUDE_CODE_ENTRYPOINT` stripped and a neutral cwd.

## What this changes in the design

The invented-content gate (merge.py:884-891) is not an obstacle to work
around -- it is the **enforcement mechanism** for the constraint the design
must now state: `--ai` is line-selection-only. The prompt states the rule; the
gate guarantees it; a rewording response is a REQUIRED must-fail test in the
response-application unit. Recorded in the DWP's review addendum
(2026-09-01__14-25-05__dev-workflow-process__ccs-merge-ai-...md).

Artifacts from the run: temp workspace only (kept path printed by the script);
nothing written under the repo besides this directory.
