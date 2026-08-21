# Triaged equivalent / don't-care survivors

Guarded authority: entries expire when the file hash heading no longer matches
git hash-object (first 12). Only separated-generation runs (modes 1-2) write here.

## dazzle_claude_config/merge.py @ 083c8c867e2b

- m3: original drops `if live.is_file():` before the directory-member yield.
  **equivalent** -- d.modified guarantees both sides existed at diff time; only
  a TOCTOU race between diff_all and the yield could expose it, which is
  outside the testable contract. (2026-08-21, mode 1.)
