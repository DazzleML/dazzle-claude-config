# Triaged equivalent / don't-care survivors

Guarded authority: entries expire when the file hash heading no longer matches
git hash-object (first 12). Only separated-generation runs (modes 1-2) write here.

## dazzle_claude_config/merge.py @ bd11a66513bb

- m3 (v0.4.0 sweep): original drops `if live.is_file():` before the
  directory-member yield. **equivalent** -- d.modified guarantees both sides
  existed at diff time; only a TOCTOU race between diff_all and the yield could
  expose it, which is outside the testable contract. (2026-08-21, mode 1;
  re-triaged under the v0.4.1 hash: the line is unchanged at merge.py:173/180.)
- M1 (v0.4.1 sweep): `shas.insert(0, head)` -> `shas.append(head)`.
  **don't-care** -- the branch fires only when HEAD is TREESAME for the path
  (omitted from `git log -- path`), so HEAD's blob equals the first listed
  commit's; the returned base bytes are identical and only the sha7 in the
  evidence label differs. (2026-08-21, mode 1.)
- M3 (v0.4.1 sweep): `if cand == ours_n:` -> `if cand == ours_n and cand != theirs_n:`.
  **equivalent** -- every caller (`two_way_labels`, `_classify`, merge) reaches
  `infer_base` only for files where ours != theirs, so no candidate can equal
  both. (2026-08-21, mode 1.)

## dazzle_claude_config/cli.py @ f31eaf694421

- M10 (v0.4.1 sweep): `if shown.returncode != 0 or not shown.stdout:` ->
  `... and not shown.stdout:`. **don't-care** -- the two differ only for an
  EMPTY committed file (rc 0, empty stdout); `infer_base` skips empty blobs,
  so both paths end in non-attribution. (2026-08-21, mode 1.)
