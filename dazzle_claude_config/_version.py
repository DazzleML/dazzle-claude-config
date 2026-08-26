"""
Version information for dazzle-claude-config.

This file is the canonical source for version numbers.
The __version__ string is automatically updated by git hooks
with build metadata (branch, build number, date, commit hash).

Format: MAJOR.MINOR.PATCH[-PHASE]_BRANCH_BUILD-YYYYMMDD-COMMITHASH
Example: 0.1.0_main_1-20260101-a1b2c3d4

Version levels:
  PROJECT_PHASE: Global project maturity (prealpha -> alpha -> beta -> stable).
       Changes rarely, when the overall project hits a threshold.
  PHASE:         Per-MINOR feature set maturity (alpha -> beta -> "" for stable).
       Drops when a MINOR's feature set is complete.
"""

# Version components - edit these for version bumps
MAJOR = 0
MINOR = 5
PATCH = 9
PHASE = ""  # Per-MINOR feature set: None, "alpha", "beta", "rc1", etc.

# Project-level phase (independent of version phase)
PROJECT_PHASE = "alpha"  # "prealpha", "alpha", "beta", "stable", or ""

# Auto-updated by git hooks - do not edit manually
__version__ = "0.5.9_main_42-20260826-6a8e37da"
__app_name__ = "dazzle-claude-config"


def get_version():
    """Return the full version string including branch and build info."""
    return __version__


def get_base_version():
    """Return the semantic version string (MAJOR.MINOR.PATCH[-PHASE])."""
    if "_" in __version__:
        return __version__.split("_")[0]
    base = f"{MAJOR}.{MINOR}.{PATCH}"
    if PHASE:
        base = f"{base}-{PHASE}"
    return base


def get_display_version():
    """Return a human-friendly version string with project phase.

    Example: 'PREALPHA 0.1.0-alpha' or 'BETA 0.5.1' or '1.0.0'
    """
    base = get_base_version()
    if PROJECT_PHASE and PROJECT_PHASE != "stable":
        return f"{PROJECT_PHASE.upper()} {base}"
    return base


def get_pip_version():
    """
    Return PEP 440 compliant version for pip/setuptools.

    Converts our version format to PEP 440:
    - Main branch: 0.1.0_main_3-20260404-hash -> 0.1.0
    - Dev branch: 0.1.0_dev_3-20260404-hash -> 0.1.0.dev3
    - Alpha: 0.1.0-alpha_main_3 -> 0.1.0a0
    """
    base = f"{MAJOR}.{MINOR}.{PATCH}"

    # Map phase to PEP 440 pre-release segment
    phase_map = {"alpha": "a0", "beta": "b0"}
    if PHASE:
        base += phase_map.get(PHASE, PHASE)

    if "_" not in __version__:
        return base

    parts = __version__.split("_")
    branch = parts[1] if len(parts) > 1 else "unknown"

    if branch == "main":
        return base
    else:
        build_info = "_".join(parts[2:]) if len(parts) > 2 else ""
        build_num = build_info.split("-")[0] if "-" in build_info else "0"
        return f"{base}.dev{build_num}"


# For convenience in imports
VERSION = get_version()
BASE_VERSION = get_base_version()
PIP_VERSION = get_pip_version()
DISPLAY_VERSION = get_display_version()
