from __future__ import annotations

import base64
from datetime import datetime

import pytest

from financial_brain.forward_edge.custody import Receipt, TrustRoot, receipt_message
from financial_brain.research.adversarial_review import (
    REVIEW_AREAS,
    AdversarialReviewError,
    AdversarialReviewJournal,
    ReviewFinding,
    make_adversarial_review,
)


def _findings(outcome="PASS"):
    return {area: ReviewFinding(outcome, f"Reviewed {area.lower()} evidence and rationale.",
                                (f"evidence:{area.lower()}",)) for area in REVIEW_AREAS}


def _args(**changes):
    values = {"review_id": "review-v1", "proposal_sha256": "a" * 64,
              "strategy_draft_sha256": "b" * 64, "proposal_author_id": "author-1",
              "reviewer_id": "reviewer-2", "reviewed_at_utc": "2026-01-01T00:00:00Z",
              "registration_at_utc": "2026-01-02T00:00:00Z", "findings": _findings(),
              "receipt": None, "trust_roots": {}}
    values.update(changes)
    return values


def _sign(private, digest, at):
    anchored = datetime.fromisoformat(at.replace("Z", "+00:00"))
    signature = base64.b64encode(private.sign(receipt_message("internal-review-service", "key-1",
                                                              digest, anchored))).decode("ascii")
    return Receipt("internal-review-service", "key-1", digest, anchored, signature)


def test_all_five_adversarial_areas_need_explicit_evidence_and_failures_reject():
    approved_without_receipt = make_adversarial_review(**_args())
    assert set(approved_without_receipt["findings"]) == set(REVIEW_AREAS)
    assert approved_without_receipt["decision"] == "NEEDS_REVISION"
    assert approved_without_receipt["registration_eligible"] is False
    assert "TRUSTED_REVIEW_RECEIPT_MISSING" in approved_without_receipt["registration_blockers"]
    failed = make_adversarial_review(**_args(findings={**_findings(), "COSTS": ReviewFinding(
        "FAIL", "Costs understated.", ("cost-sensitivity:2x",))}))
    assert failed["decision"] == "REJECTED"
    assert failed["failed_areas"] == ["COSTS"]


def test_signed_distinct_reviewer_can_approve_only_before_registration():
    crypto = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    private = crypto.Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(encoding=serialization.Encoding.Raw,
                                               format=serialization.PublicFormat.Raw)
    roots = {("internal-review-service", "key-1"): TrustRoot(
        "internal-review-service", "key-1", base64.b64encode(public).decode("ascii"))}
    preview = make_adversarial_review(**_args(trust_roots=roots))
    receipt = _sign(private, preview["review_sha256"], "2026-01-01T00:01:00Z")
    review = make_adversarial_review(**_args(trust_roots=roots, receipt=receipt))
    assert review["decision"] == "APPROVED"
    assert review["registration_eligible"] is True
    assert review["strategy_mutated"] is False
    late = make_adversarial_review(**_args(
        reviewed_at_utc="2026-01-01T00:00:40Z",
        registration_at_utc="2026-01-01T00:00:30Z", trust_roots=roots, receipt=receipt))
    assert late["registration_eligible"] is False
    assert "REVIEW_NOT_BEFORE_REGISTRATION" in late["registration_blockers"]


def test_self_review_and_incomplete_criteria_never_approve():
    self_review = make_adversarial_review(**_args(reviewer_id="author-1"))
    assert self_review["decision"] == "NEEDS_REVISION"
    assert "REVIEWER_NOT_INDEPENDENT" in self_review["registration_blockers"]
    incomplete = make_adversarial_review(**_args(findings={
        **_findings(), "FALSIFIER": ReviewFinding("INSUFFICIENT", "No falsifier test yet.", ("plan:2",))
    }))
    assert incomplete["decision"] == "NEEDS_REVISION"
    assert incomplete["insufficient_areas"] == ["FALSIFIER"]


def test_missing_review_evidence_is_rejected():
    findings = _findings()
    findings["LEAKAGE"] = ReviewFinding("PASS", "No evidence reference.", ())
    with pytest.raises(AdversarialReviewError, match="evidence IDs"):
        make_adversarial_review(**_args(findings=findings))
    findings = _findings()
    del findings["COSTS"]
    with pytest.raises(AdversarialReviewError, match="COSTS"):
        make_adversarial_review(**_args(findings=findings))


def test_review_journal_is_idempotent_and_immutable(tmp_path):
    review = make_adversarial_review(**_args())
    journal = AdversarialReviewJournal(tmp_path / "reviews.jsonl")
    digest = journal.record(review)
    assert journal.record(review) == digest
    with pytest.raises(AdversarialReviewError, match="immutable"):
        journal.record({**review, "decision": "APPROVED"})
