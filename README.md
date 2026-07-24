# dazzle-claude-config

[![Installs](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/djdarcy/5329fbe75d9bbd8597cdc45863a22878/raw/installs.json)](https://dazzleml.github.io/dazzle-claude-config/stats/#installs)

**ccs** -- sync Claude Code configuration across machines.

Your global `CLAUDE.md`, agents, skills, commands, hooks, and settings live in a git "payload" repo (yours is private; GitHub is the distribution area). `ccs` moves config between that repo's checkout and your live `~/.claude` + `~/claude` directories -- guarded, backed up, and never touching your home directory's own git repository.

Part of the DazzleML Claude toolchain: [session-logger](https://github.com/DazzleML/claude-session-logger) records, [claude-session-backup](https://github.com/DazzleML/Claude-Session-Backup) preserves, **ccs distributes**.

## Install

```bash
pip install dazzle-claude-config
```

Installs the `ccs` command (alias: `dazzle-claude-config`). Python 3.10+, stdlib only.

## Usage

```bash
git clone <your-payload-repo> ~/claude/<payload-name>   # once per machine

ccs status     # three-way drift report (live vs checkout vs remote); exit 1 = drift
ccs diff       # per-file details
ccs collect    # live config INTO the checkout (secrets refused + reported)
ccs apply      # checkout config INTO the live tree (originals backed up first)
```

`collect` and `apply` support `--dry-run`. `apply` supports `--only <prefix>` and `--sync-removals` (staged to the backup dir -- nothing is ever deleted in place). Point at a non-default checkout with `--checkout-dir`; override territories with `--claude-dir` / `--user-claude` (`CLAUDE_CONFIG_DIR` is honored).

## How it works

- **Manifest-driven allowlist**: the payload repo's `ccs-manifest.json` declares what syncs where (territories, per-entry strategy: `copy`, `seed-if-absent`, `render`, `plugins`). Nothing outside the manifest ever moves.
- **Secrets are structurally fenced**: a hard deny-list (`.credentials.json`, `.claude.json`, `settings.local.json`, databases, plugin caches...) refuses files even if listed, and collect scans content for credential shapes (`sk-ant-`, `ghp_`, AWS keys, private-key headers...) -- refusals are reported, never silent.
- **Git is the merge arena**: multi-machine conflicts are ordinary git conflicts in the checkout; `apply` refuses while conflicts are unresolved. `ccs` contains no merge logic and exposes no branch operations.
- **The home repo is untouchable**: if your home directory is itself a git repository (e.g. managed by claude-session-backup), `ccs` structurally refuses to operate on it.
- **Nothing is destroyed**: every overwrite is preceded by a timestamped backup under `~/claude/backups/ccs/`; removals are staged there, never deleted in place.
- **Index verification**: files copied into the checkout are checked against git's ignore/exclude mechanism, so a machine-level exclude can never silently drop config from the payload.

## Roadmap

`render` (templated settings with per-OS/per-machine overlays), declarative plugin install, and `ccs bootstrap <payload-url>` (one-command machine onboarding) land in Phase 2 -- see the pinned [Roadmap issue](https://github.com/DazzleML/dazzle-claude-config/issues/4).

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
