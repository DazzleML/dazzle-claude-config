# dazzle-claude-config

A new project created from git-repokit-template

## Installation

```bash
pip install dazzle_claude_config
```

### From Source

```bash
git clone https://github.com/DazzleML/dazzle-claude-config.git
cd dazzle-claude-config
pip install -e ".[dev]"
```

## Usage

```bash
dazzle-claude-config --help
```

## Development

```bash
# Clone and install
git clone https://github.com/DazzleML/dazzle-claude-config.git
cd dazzle-claude-config
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Install git hooks (if using repokit-common submodule)
bash scripts/repokit-common/install-hooks.sh
```

## License

GPL-3.0-or-later. See [LICENSE](LICENSE) for details.

