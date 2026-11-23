from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import release_provenance
from scripts.release_provenance import TEST_ARTIFACTS, build_manifest


def _repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for artifact in TEST_ARTIFACTS:
        (root / artifact).write_text(f"artifact: {artifact}\n", encoding="utf-8")
    for directory in ("src/financial_brain/strategies", "src/financial_brain/patterns"):
        path = root / directory
        path.mkdir(parents=True)
        (path / "rule.py").write_text("RULE = 1\n", encoding="utf-8")
    for rel in (
        "src/financial_brain/evaluation/swing.py", "src/financial_brain/paper/intraday.py",
        "src/financial_brain/paper/intraday_runner.py", "src/financial_brain/paper/zoo_edges.py",
        "src/financial_brain/forward_edge/registry.py",
        "src/financial_brain/forward_edge/strategy_adapter.py",
        "src/financial_brain/forward_edge/strategy_pipeline.py",
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("RULE = 1\n", encoding="utf-8")
    docs = root / "docs/edge-program"
    docs.mkdir(parents=True)
    (docs / "CHANGELOG.md").write_text("# changes\n", encoding="utf-8")
    (docs / "market-data-rights-review.v1.md").write_text("rights unknown\n", encoding="utf-8")
    (docs / "data-permissions.v1.json").write_text(json.dumps({"permissions": [
        {"status": "UNKNOWN_RIGHTS"}, {"status": "USE_ALLOWED"},
    ]}), encoding="utf-8")


def test_release_manifest_binds_test_strategy_changelog_and_rights_inputs(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _repo(tmp_path)
    sha = "a" * 40
    monkeypatch.setattr(release_provenance, "_git", lambda _root, *args:
                        sha if args == ("rev-parse", "HEAD") else "main" if args == ("branch", "--show-current") else "")
    output = tmp_path / "out/release.json"
    manifest = build_manifest(tmp_path, output=output)
    assert manifest["source"]["commit_sha"] == sha
    assert set(manifest["test_artifacts"]) == set(TEST_ARTIFACTS)
    assert manifest["strategy_rules"]["file_count"] == 9
    assert manifest["data_rights"]["posture"] == "RESTRICTED_UNRESOLVED_RIGHTS"
    assert manifest["data_rights"]["authorization_inferred"] is False
    assert json.loads(output.read_text(encoding="utf-8")) == manifest


def test_release_manifest_refuses_missing_test_artifact(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _repo(tmp_path)
    monkeypatch.setattr(release_provenance, "_git", lambda _root, *args: "a" * 40 if args == (
        "rev-parse", "HEAD") else "")
    (tmp_path / TEST_ARTIFACTS[0]).unlink()
    with pytest.raises(ValueError, match="test artifact is missing"):
        build_manifest(tmp_path)
