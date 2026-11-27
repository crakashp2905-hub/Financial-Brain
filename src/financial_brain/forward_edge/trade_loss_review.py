"""Evidence-backed per-trade loss review; narrative cannot stand without source references."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping


class TradeLossReviewError(ValueError):
    """Loss-review record is incomplete, unsupported or not a losing trade."""


class LossCause(StrEnum):
    DATA = "DATA"
    MODEL = "MODEL"
    CONTRACT = "CONTRACT"
    EXECUTION = "EXECUTION"
    EXIT = "EXIT"
    SIZING = "SIZING"
    MULTIPLE = "MULTIPLE"
    UNRESOLVED = "UNRESOLVED"


REQUIRED_DOMAINS = frozenset({"data", "model", "contract", "execution", "exit", "sizing"})
_SHA = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class TradeLossReview:
    trade_id: str
    net_pnl_inr: float
    reviewer_id: str
    reviewed_at: datetime
    root_cause: LossCause
    finding: str
    source_sha256_by_domain: Mapping[str, tuple[str, ...]]


def build_trade_loss_review(review: TradeLossReview) -> dict[str, object]:
    """Create a hash-addressed review only when every cause domain cites source evidence."""
    if not review.trade_id.strip() or not review.reviewer_id.strip():
        raise TradeLossReviewError("trade and reviewer identities are required")
    if not math.isfinite(review.net_pnl_inr) or review.net_pnl_inr >= 0:
        raise TradeLossReviewError("loss review requires finite negative net trade P&L")
    if review.reviewed_at.tzinfo is None or review.reviewed_at.utcoffset() is None:
        raise TradeLossReviewError("review timestamp must be timezone-aware")
    if not isinstance(review.root_cause, LossCause) or not review.finding.strip():
        raise TradeLossReviewError("root-cause classification and finding are required")
    if set(review.source_sha256_by_domain) != REQUIRED_DOMAINS:
        missing = sorted(REQUIRED_DOMAINS - set(review.source_sha256_by_domain))
        extra = sorted(set(review.source_sha256_by_domain) - REQUIRED_DOMAINS)
        raise TradeLossReviewError(f"source references must cover all loss domains; missing={missing}, extra={extra}")
    evidence: dict[str, list[str]] = {}
    for domain, references in sorted(review.source_sha256_by_domain.items()):
        if not references or any(not _SHA.fullmatch(value) for value in references):
            raise TradeLossReviewError(f"{domain} requires at least one valid source SHA-256 reference")
        evidence[domain] = sorted(set(references))
    body: dict[str, object] = {
        "trade_id": review.trade_id,
        "net_pnl_inr": review.net_pnl_inr,
        "reviewer_id": review.reviewer_id,
        "reviewed_at": review.reviewed_at.isoformat(),
        "root_cause": review.root_cause.value,
        "finding": review.finding,
        "source_sha256_by_domain": evidence,
        "domains_reviewed": sorted(REQUIRED_DOMAINS),
        "review_status": "EVIDENCE_REFERENCED_LOCAL_REVIEW",
        "independently_attested": False,
    }
    body["review_sha256"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()
    return body
