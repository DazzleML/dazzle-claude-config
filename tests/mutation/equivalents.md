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

## dazzle_claude_config/basefind.py @ dd3c133d6ee9

- M4 (v0.4.3 sweep): `Region(..., [ours[b2o[i]] ...])` -> `[base[i] ...]`.
  **equivalent** -- `b2o` is built from `equal` opcodes only, so every mapped
  ours line is byte-identical to its base line; the region text is the same
  list either way. (2026-08-22, mode 1.)
- M5 (v0.4.3 sweep): `if any(mask[at:at+n])` -> `if all(...)` in
  `wrap_clean_regions`. **equivalent** -- a region is found by `_find` as a
  contiguous run of its own lines, none of which is a marker line, so its span
  can never cross a hunk boundary; the mask is therefore uniform over the span
  and `any` == `all`. (2026-08-22, mode 1.)

## dazzle_claude_config/syncmap.py @ 8694a5c0fd05

- M9 (v0.4.3 sweep): `rel_in_scope`: `if sub_prefix is None` -> `if not sub_prefix`.
  **equivalent** -- the only producer of a sub-prefix is `only_scope`, which
  strips trailing slashes before slicing, so `--only dotclaude/skills/` is
  the whole entry (`(True, None)`) and an empty-string sub-prefix can never
  reach `rel_in_scope`. Pinned by the trailing-slash case in
  `test_only_scope`. (2026-08-22, mode 1.)

## dazzle_claude_config/gitops.py @ 25ced351a047

- G3 (v0.5.8 sweep): `dirty_paths` parses the porcelain line with `line[3:]`;
  the mutant uses `line[2:]`. **equivalent** -- both slices are followed by
  `.strip()`, and porcelain's format is two status columns, one space, then the
  path, so `line[2:].strip()` and `line[3:].strip()` return the identical
  string for every status code including `??`. No input can separate them.
  (2026-08-26, diff-scoped sweep.)

## dazzle_claude_config/gitops.py @ e58d26e552bb

- H3 (v0.5.9 sweep): the rename unpack `old_p, new_p = path.split(" -> ", 1)`
  with the two names swapped. **equivalent** -- both names are added to the
  same set on the next two lines and nothing downstream distinguishes them,
  so no input can separate the two spellings. Predicted equivalent in the
  spec before the run rather than triaged afterwards. (2026-08-26.)
