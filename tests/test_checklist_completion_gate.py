from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.checklist_completion_gate import build_report, validate_changed_complete_claims


def test_complete_item_requires_docs_tests_and_hashed_acceptance_artifact(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()
    doc = tmp_path / "docs" / "proof.md"
    test = tmp_path / "tests" / "test_proof.py"
    artifact = tmp_path / "acceptance.json"
    doc.write_text("Documentation", encoding="utf-8")
    test.write_text("def test_proof(): pass\n", encoding="utf-8")
    artifact.write_text('{"accepted": true}\n', encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    status = {
        "items": [
            {
                "id": "PA-00-01",
                "status": "COMPLETE",
                "evidence": ["docs/proof.md"],
                "test_evidence": ["python -m pytest tests/test_proof.py -q: 1 passed"],
                "acceptance_artifacts": [{"path": "acceptance.json", "sha256": digest}],
            }
        ]
    }

    report = build_report(status, tmp_path)

    assert report["complete_items"] == 1
    assert report["items_with_evidence_gaps"] == 0


def test_existing_module_alone_cannot_support_complete_claim(tmp_path: Path) -> None:
    status = {"items": [{"id": "PA-00-01", "status": "COMPLETE", "evidence": ["src/module.py"]}]}

    report = build_report(status, tmp_path)

    assert report["items_with_evidence_gaps"] == 1
    assert set(report["gaps"][0]["gaps"]) == {
        "missing_existing_documentation_reference",
        "missing_recorded_test_evidence",
        "missing_existing_executable_test_artifact",
        "missing_hashed_acceptance_artifact",
    }


def test_artifact_hash_mismatch_is_reported(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs/proof.md").write_text("doc", encoding="utf-8")
    (tmp_path / "tests/test_proof.py").write_text("test", encoding="utf-8")
    artifact = tmp_path / "acceptance.json"
    artifact.write_text("actual", encoding="utf-8")
    status = {
        "items": [
            {
                "id": "PA-00-01",
                "status": "COMPLETE",
                "evidence": ["docs/proof.md"],
                "test_evidence": ["tests/test_proof.py passed"],
                "acceptance_artifacts": [{"path": "acceptance.json", "sha256": "0" * 64}],
            }
        ]
    }

    report = build_report(status, tmp_path)

    assert report["gaps"][0]["gaps"] == ["acceptance_artifact_missing_or_hash_mismatch"]


def test_changed_complete_claim_is_rejected_until_evidence_is_complete(tmp_path: Path) -> None:
    base = {"items": [{"id": "PA-00-01", "status": "PARTIAL", "evidence": []}]}
    current = {"items": [{"id": "PA-00-01", "status": "COMPLETE", "evidence": ["src/module.py"]}]}

    failures = validate_changed_complete_claims(current, base, tmp_path)

    assert failures[0]["id"] == "PA-00-01"
    assert "missing_hashed_acceptance_artifact" in failures[0]["gaps"]


def test_unchanged_legacy_complete_claim_does_not_block_new_transitions(tmp_path: Path) -> None:
    legacy = {"id": "FB-0001", "status": "COMPLETE", "evidence": ["src/module.py"]}

    failures = validate_changed_complete_claims({"items": [legacy]}, {"items": [legacy]}, tmp_path)

    assert failures == []
