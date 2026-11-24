from __future__ import annotations

import hashlib

from scripts.build_checklist_evidence_bundle import OUTPUT, build_bytes
from financial_brain.forward_edge.evidence_bundle import verify_evidence_bundle


def test_checklist_release_bundle_is_byte_reproducible() -> None:
    first = build_bytes()
    second = build_bytes()
    assert first == second
    assert len(hashlib.sha256(first).hexdigest()) == 64


def test_committed_checklist_release_bundle_verifies() -> None:
    verified = verify_evidence_bundle(OUTPUT)
    assert verified.signature_status == "UNSIGNED"
    assert verified.file_count >= 20
