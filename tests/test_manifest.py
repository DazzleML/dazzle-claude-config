import json

import pytest

from dazzle_claude_config.manifest import Manifest, ManifestError


def test_loads_valid_manifest(env):
    *_, manifest, _ = env
    assert manifest.version == 1
    assert len(manifest.copy_entries()) == 3
    assert len(manifest.seed_entries()) == 1
    assert [e.strategy for e in manifest.deferred_entries()] == ["render"]


def _write_manifest(checkout, data):
    (checkout / "ccs-manifest.json").write_text(json.dumps(data), encoding="utf-8")


def test_unknown_top_key_rejected(env):
    _, _, checkout, _, _ = env
    _write_manifest(checkout, {"manifest_version": 1, "territories": {},
                               "entries": [], "surprise": True})
    with pytest.raises(ManifestError, match="unknown manifest keys"):
        Manifest.load(checkout)


def test_bad_strategy_rejected(env):
    _, _, checkout, _, _ = env
    _write_manifest(checkout, {
        "manifest_version": 1,
        "territories": {"t": {"root_var": "CLAUDE_DIR", "repo_dir": "d"}},
        "entries": [{"repo": "d/x", "territory": "t", "target": "x",
                     "strategy": "symlink"}]})
    with pytest.raises(ManifestError, match="invalid strategy"):
        Manifest.load(checkout)


def test_wrong_version_rejected(env):
    _, _, checkout, _, _ = env
    _write_manifest(checkout, {"manifest_version": 99})
    with pytest.raises(ManifestError, match="unsupported manifest_version"):
        Manifest.load(checkout)


def test_missing_manifest_rejected(tmp_path):
    with pytest.raises(ManifestError, match="manifest not found"):
        Manifest.load(tmp_path)
