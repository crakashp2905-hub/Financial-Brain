"""Independent pre-registration challenge record for proposed strategy changes."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from financial_brain.forward_edge.custody import (
    CustodyStatus,
    Receipt,
    TrustRoot,
    classify_custody,
)
from financial_brain.forward_edge.durable import DurableJournal


REVIEW_AREAS = ("LEAKAGE", "COSTS", "MULTIPLE_TESTING", "CHANCE_FITTING", "FALSIFIER")
OUTCOMES = {"PASS", "FAIL", "INSUFFICIENT"}


class AdversarialReviewError(ValueError):
    """Invalid review record or identity/custody evidence."""


def _utc(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise AdversarialReviewError(f"{name} must be timezone-aware ISO time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdversarialReviewError(f"{name} must be timezone-aware ISO time")
    return parsed.astimezone(timezone.utc)


def _sha(value: str, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise AdversarialReviewError(f"{name} must be lowercase SHA-256")


@dataclass(frozen=True)
class ReviewFinding:
    outcome: str
    rationale: str
    evidence_ids: tuple[str, ...]


def make_adversarial_review(
    *, review_id: str,
    proposal_sha256: str,
    strategy_draft_sha256: str,
    proposal_author_id: str,
    reviewer_id: str,
    reviewed_at_utc: str,
    registration_at_utc: str,
    findings: Mapping[str, ReviewFinding],
    receipt: Receipt | None,
    trust_roots: Mapping[tuple[str, str], TrustRoot],
) -> dict:
    """Review before registration and return a gate, never a mutable strategy action.

    A trusted review receipt signs the exact reviewer identity, proposal digests,
    findings, and evidence IDs before the declared registration time.
    """
    if not isinstance(review_id, str) or not review_id.strip():
        raise AdversarialReviewError("review_id is required")
    if not isinstance(proposal_author_id, str) or not proposal_author_id.strip():
        raise AdversarialReviewError("proposal_author_id is required")
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise AdversarialReviewError("reviewer_id is required")
    independent = reviewer_id != proposal_author_id
    _sha(proposal_sha256, "proposal_sha256")
    _sha(strategy_draft_sha256, "strategy_draft_sha256")
    reviewed = _utc(reviewed_at_utc, "reviewed_at_utc")
    registration = _utc(registration_at_utc, "registration_at_utc")
    findings_data = {}
    for area in REVIEW_AREAS:
        finding = findings.get(area)
        if not isinstance(finding, ReviewFinding):
            raise AdversarialReviewError(f"missing review finding for {area}")
        if finding.outcome not in OUTCOMES:
            raise AdversarialReviewError(f"invalid outcome for {area}")
        if not isinstance(finding.rationale, str) or not finding.rationale.strip():
            raise AdversarialReviewError(f"review rationale is required for {area}")
        if not finding.evidence_ids or any(not isinstance(i, str) or not i.strip() for i in finding.evidence_ids):
            raise AdversarialReviewError(f"evidence IDs are required for {area}")
        findings_data[area] = {"outcome": finding.outcome, "rationale": finding.rationale,
                               "evidence_ids": list(finding.evidence_ids)}
    body = {
        "review_id": review_id, "proposal_sha256": proposal_sha256,
        "strategy_draft_sha256": strategy_draft_sha256,
        "proposal_author_id": proposal_author_id, "reviewer_id": reviewer_id,
        "reviewer_independent_of_author": independent,
        "reviewed_at_utc": reviewed.isoformat(), "registration_at_utc": registration.isoformat(),
        "findings": findings_data,
    }
    review_sha = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode()).hexdigest()
    receipt_status = (classify_custody(
        manifest_sha256=review_sha, decision_at_utc=reviewed,
        entry_earliest_at_utc=registration, receipt=receipt, trust_roots=trust_roots,
    ) if registration > reviewed else CustodyStatus.CONFLICTED)
    if registration <= reviewed:
        temporal_valid = False
    else:
        temporal_valid = True
    failed = [area for area, row in findings_data.items() if row["outcome"] == "FAIL"]
    insufficient = [area for area, row in findings_data.items() if row["outcome"] == "INSUFFICIENT"]
    if failed:
        decision = "REJECTED"
    elif insufficient:
        decision = "NEEDS_REVISION"
    else:
        decision = "APPROVED" if independent and temporal_valid and receipt_status is CustodyStatus.VERIFIED else "NEEDS_REVISION"
    eligible = decision == "APPROVED"
    return {
        **body,
        "review_sha256": review_sha,
        "review_receipt": (None if receipt is None else {
            "provider_id": receipt.provider_id, "key_id": receipt.key_id,
            "digest_sha256": receipt.digest_sha256,
            "anchored_at_utc": receipt.anchored_at_utc.astimezone(timezone.utc).isoformat(),
            "signature_b64": receipt.signature_b64,
        }),
        "receipt_status": receipt_status.value,
        "failed_areas": failed,
        "insufficient_areas": insufficient,
        "decision": decision,
        "registration_eligible": eligible,
        "registration_blockers": [] if eligible else [
            *(["REVIEWER_NOT_INDEPENDENT"] if not independent else []),
            *(["REVIEW_NOT_BEFORE_REGISTRATION"] if not temporal_valid else []),
            *(["TRUSTED_REVIEW_RECEIPT_MISSING"] if receipt_status is not CustodyStatus.VERIFIED else []),
            *(["REVIEW_FINDINGS_NOT_ALL_PASS"] if failed or insufficient else []),
        ],
        "strategy_mutated": False,
    }


class AdversarialReviewJournal:
    """Append-only review decisions keyed by their immutable review ID."""

    def __init__(self, path: Path) -> None:
        self.journal = DurableJournal(path)

    def record(self, review: Mapping[str, object]) -> str:
        if not isinstance(review.get("review_id"), str) or not review.get("review_id"):
            raise AdversarialReviewError("review_id is required")
        body = dict(review)
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                          allow_nan=False).encode()).hexdigest()
        record = {"t": "ADVERSARIAL_PRE_REGISTRATION_REVIEW", **body, "archive_sha256": digest}
        prior = next((item for item in self.journal.records()
                      if item.get("review_id") == review["review_id"]), None)
        if prior is not None:
            if prior != record:
                raise AdversarialReviewError("review identity is immutable")
            return digest
        self.journal.append(record)
        return digest
