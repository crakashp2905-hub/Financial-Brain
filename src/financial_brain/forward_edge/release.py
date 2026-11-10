"""Immutable release labels (FE-0152): no artifact may advertise a higher stage than its evidence permits."""
from __future__ import annotations

from enum import IntEnum


class ReleaseLabel(IntEnum):
    PAPER_READY = 1
    FORWARD_VERIFIED = 2
    BROKER_READY = 3
    LIVE_APPROVED = 4


class LabelError(PermissionError):
    pass


def highest_label(evidence: dict) -> ReleaseLabel | None:
    """Each label needs everything below it plus its own evidence. Missing keys count as not met."""
    def has(*keys: str) -> bool:
        return all(evidence.get(k) is True for k in keys)

    if not has("tests_green", "protected_records_intact", "physical_orders_disabled"):
        return None
    label = ReleaseLabel.PAPER_READY
    if has("independent_custody_verified", "promotion_grade_verdict") and evidence.get("verified_sessions", 0) >= 60:
        label = ReleaseLabel.FORWARD_VERIFIED
    else:
        return label
    if not has("broker_compliance_verified", "rehearsal_gate_open"):
        return label
    label = ReleaseLabel.BROKER_READY
    if has("owner_live_approval_valid"):
        label = ReleaseLabel.LIVE_APPROVED
    return label


def assert_label_allowed(claimed: ReleaseLabel, evidence: dict) -> None:
    top = highest_label(evidence)
    if top is None or claimed > top:
        raise LabelError(f"{claimed.name} is not supported by the evidence (highest allowed: {top.name if top else 'NONE'})")
