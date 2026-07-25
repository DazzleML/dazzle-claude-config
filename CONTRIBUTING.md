# Contributing to dazzle-claude-config

Thank you for considering contributing to dazzle-claude-config!

## Development Setup

### Prerequisites

- **Python 3.10+**
- **Git**

### Clone and Install

```bash
git clone https://github.com/DazzleML/dazzle-claude-config.git
cd dazzle-claude-config
python -m venv .venv
source .venv/bin/activate        # Linux/Mac
# or: .venv\Scripts\activate     # Windows
pip install -e ".[dev]"
```

### Run Tests

```bash
python -m pytest tests/ -v
```

### Check syntax against the minimum supported Python

`pyproject.toml` declares `requires-python = ">=3.10"`, but most contributors
develop on 3.12+. Some syntax is accepted there and is a **SyntaxError** on
3.10/3.11 -- notably PEP 701 f-strings (reusing the enclosing quote character,
or a replacement field spanning multiple lines). Such a package installs fine
and then fails at import, which no local test run will reveal.

Run this before pushing -- it reproduces the failure on any interpreter:

```bash
pip install "ruff>=0.16"
ruff check --target-version py310 --select E9 --isolated dazzle_claude_config/ tests/
```

Older ruff releases do not detect these; 0.16+ is required. CI runs the same
check ahead of the test matrix.

## Project Structure

```
dazzle_claude_config/
  __init__.py         # Package initialization
  __main__.py         # CLI entry (python -m dazzle_claude_config)
  _version.py         # Version (PEP 440)
tests/
  conftest.py         # Shared fixtures
  test_*.py           # Test files
  one-offs/           # Quick checks, proof-of-concept scripts
scripts/
  repokit-common/     # Shared tools (git submodule)
```

## Key Design Principles

1. **Tests are important** -- write tests for new features
2. **One-offs graduate** -- quick tests in `tests/one-offs/` can be promoted to proper tests
3. **Cross-platform** -- works on Windows, Linux, macOS
4. **Clean commits** -- use conventional commit format
