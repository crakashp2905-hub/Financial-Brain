"""Single evidence-tier labels with qualification gates for forward reports (FE-0102)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ClaimTier(StrEnum):
    HISTORICAL = "HISTORICAL"
    SHADOW = "SHADOW"
    LOCAL_PAPER = "LOCAL_PAPER"
    INDEPENDENT_FORWARD = "INDEPENDENT_FORWARD"
    DEPLOYMENT_VALIDATED = "DEPLOYMENT_VALIDATED"


class ClaimTierError(ValueError):
    """Evidence does not qualify for the requested claim tier."""


@dataclass(frozen=True)
class TierEvidence:
    historical_replay: bool = False
    complete_shadow_log: bool = False
    local_paper_reconciled: bool = False
    independent_receipts_verified: bool = False
    eligible_independent_sessions: int = 0
    deployment_broker_reconciled: bool = False
    compliance_verified: bool = False
    human_approval_recorded: bool = False


def qualify_claim(tier: ClaimTier, evidence: TierEvidence) -> str:
    """Return the one permitted tier label or reject an unsupported evidence claim."""
    if tier == ClaimTier.HISTORICAL and not evidence.historical_replay:
        raise ClaimTierError("HISTORICAL requires an explicit replay/backtest source")
    if tier == ClaimTier.SHADOW and not evidence.complete_shadow_log:
        raise ClaimTierError("SHADOW requires a complete passive opportunity log")
    if tier == ClaimTier.LOCAL_PAPER and not evidence.local_paper_reconciled:
        raise ClaimTierError("LOCAL_PAPER requires a reconciled local paper book")
    if tier == ClaimTier.INDEPENDENT_FORWARD:
        if not evidence.independent_receipts_verified:
            raise ClaimTierError("INDEPENDENT_FORWARD requires verified external timestamp receipts")
        if evidence.eligible_independent_sessions < 60:
            raise ClaimTierError("INDEPENDENT_FORWARD requires at least 60 eligible sessions")
    if tier == ClaimTier.DEPLOYMENT_VALIDATED:
        if not (evidence.deployment_broker_reconciled and evidence.compliance_verified
                and evidence.human_approval_recorded):
            raise ClaimTierError("DEPLOYMENT_VALIDATED requires broker reconciliation, compliance and human approval")
    return tier.value


def render_tier_badge(tier: ClaimTier, evidence: TierEvidence) -> str:
    """Render exactly one qualified evidence tier for a report/table/chart caption."""
    return f"Evidence tier: {qualify_claim(tier, evidence)}"


def validate_artifact_tier_labels(labels: tuple[str, ...]) -> str:
    """Guard existing renderers that may provide labels as a list rather than a scalar."""
    if len(labels) != 1:
        raise ClaimTierError("every artifact must declare exactly one evidence tier")
    try:
        return ClaimTier(labels[0]).value
    except ValueError as exc:
        raise ClaimTierError("unknown evidence tier label") from exc
