# p0: can a model's hunk resolution pass ccs's validation gate?

Pre-registered 2026-09-01, BEFORE the first run. Recommended by the adversarial
review of the ai-merge DWP (finding 4: merge.py:884-891 rejects any line not
found verbatim in ours + theirs + base).

## The question

The `--ai` design assumes a model can resolve a dual-touched hunk and the
result can pass `validate()`. The invented-content check appears to forbid the
very thing models naturally do -- harmonize wording. Is **line-selection-only**
a sufficient and enforceable constraint?

## The arms

Both arms: same three-way fixture (base/ours/theirs .md), `.merged` produced by
`git merge-file -p --diff3` (the same mechanism `seed()` uses), ONE dual-touched
conflict block extracted and sent to a live `claude -p` (env vars
CLAUDECODE/CLAUDE_CODE_ENTRYPOINT stripped, neutral cwd per review finding 9),
response spliced back in place of the block, `validate()` run on the file.

- **Arm A (control, designed to fail):** the prompt asks the model to write a
  single harmonized line combining both sides.
- **Arm B:** the prompt permits ONLY complete lines exactly as they appear in
  the panes -- select and order, never edit or invent.

## Pass criterion (written before running)

- **PASS for the design** = Arm A fails validation with the "appear in neither
  side nor the base" message, AND Arm B passes validation with zero failures
  and no remaining markers.
- **Arm B fails** => line-selection is NOT sufficient; a5b must add a
  validation mode, and the DWP's a5 contract is wrong as written.
- **Arm A passes** => the gate is weaker than merge.py:884 reads; re-examine
  the check before trusting it as the safety property.
- Secondary recordings, not gating: prompt byte sizes, wall time per call,
  whether `claude -p` works at all from inside a Claude Code session with the
  env stripped.
