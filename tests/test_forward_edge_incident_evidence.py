from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from financial_brain.forward_edge.incident_evidence import (
    IncidentEvidenceError,
    capture_first_loss,
    verify_first_loss,
)


def _evidence() -> dict[str, bytes]:
    return {key: f"raw-{key}".encode() for key in ("quote", "signal", "risk", "execution", "ledger")}


def test_first_loss_capsule_persists_and_verifies_raw_artifacts():
    with tempfile.TemporaryDirectory(prefix="fbie-") as directory:
        root = Path(directory)
        manifest = capture_first_loss(root, incident_id="loss-001", occurred_at="2026-11-27T10:00:00+05:30",
                                      evidence=_evidence())
        assert manifest["claim_scope"] == "LOCAL_TAMPER_EVIDENT_ONLY"
        assert verify_first_loss(root, "loss-001")
        assert len(list((root / "blobs").glob("*.bin"))) == 5


def test_incident_id_replay_must_match_and_blobs_cannot_be_overwritten():
    with tempfile.TemporaryDirectory(prefix="fbie-") as directory:
        root = Path(directory)
        evidence = _evidence()
        capture_first_loss(root, incident_id="loss-002", occurred_at="before-repair", evidence=evidence)
        with pytest.raises(IncidentEvidenceError, match="conflict"):
            capture_first_loss(root, incident_id="loss-002", occurred_at="before-repair",
                               evidence={**evidence, "quote": b"changed quote"})
        assert verify_first_loss(root, "loss-002")


def test_verifier_detects_blob_corruption_and_capture_requires_all_categories():
    with tempfile.TemporaryDirectory(prefix="fbie-") as directory:
        root = Path(directory)
        manifest = capture_first_loss(root, incident_id="loss-003", occurred_at="before-repair",
                                      evidence=_evidence())
        with pytest.raises(IncidentEvidenceError, match="categories mismatch"):
            capture_first_loss(root, incident_id="loss-004", occurred_at="before-repair",
                               evidence={"quote": b"quote"})
        item = manifest["evidence"]["risk"]
        (root / item["blob"]).write_bytes(b"tampered")
        assert not verify_first_loss(root, "loss-003")


def test_manifest_refuses_path_traversal_ids():
    with tempfile.TemporaryDirectory(prefix="fbie-") as directory:
        with pytest.raises(IncidentEvidenceError, match="safe"):
            capture_first_loss(Path(directory), incident_id="../outside", occurred_at="t", evidence=_evidence())
