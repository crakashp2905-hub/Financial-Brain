import pytest

from financial_brain.forward_edge.claim_tier import (
    ClaimTier,
    ClaimTierError,
    TierEvidence,
    qualify_claim,
    render_tier_badge,
    validate_artifact_tier_labels,
)


def test_each_tier_requires_its_own_evidence_and_returns_one_label() -> None:
    assert qualify_claim(ClaimTier.HISTORICAL, TierEvidence(historical_replay=True)) == "HISTORICAL"
    assert render_tier_badge(ClaimTier.SHADOW, TierEvidence(complete_shadow_log=True)) == "Evidence tier: SHADOW"
    assert qualify_claim(ClaimTier.LOCAL_PAPER, TierEvidence(local_paper_reconciled=True)) == "LOCAL_PAPER"
    assert qualify_claim(ClaimTier.INDEPENDENT_FORWARD, TierEvidence(
        independent_receipts_verified=True, eligible_independent_sessions=60)) == "INDEPENDENT_FORWARD"
    assert qualify_claim(ClaimTier.DEPLOYMENT_VALIDATED, TierEvidence(
        deployment_broker_reconciled=True, compliance_verified=True,
        human_approval_recorded=True)) == "DEPLOYMENT_VALIDATED"


def test_higher_tier_cannot_be_claimed_from_local_only_or_fixture_data() -> None:
    with pytest.raises(ClaimTierError, match="external timestamp"):
        qualify_claim(ClaimTier.INDEPENDENT_FORWARD,
                      TierEvidence(local_paper_reconciled=True, eligible_independent_sessions=100))
    with pytest.raises(ClaimTierError, match="60 eligible"):
        qualify_claim(ClaimTier.INDEPENDENT_FORWARD,
                      TierEvidence(independent_receipts_verified=True, eligible_independent_sessions=59))
    with pytest.raises(ClaimTierError, match="broker reconciliation"):
        qualify_claim(ClaimTier.DEPLOYMENT_VALIDATED, TierEvidence())


def test_artifact_has_exactly_one_valid_tier_label() -> None:
    assert validate_artifact_tier_labels(("LOCAL_PAPER",)) == "LOCAL_PAPER"
    with pytest.raises(ClaimTierError, match="exactly one"):
        validate_artifact_tier_labels(())
    with pytest.raises(ClaimTierError, match="exactly one"):
        validate_artifact_tier_labels(("HISTORICAL", "LOCAL_PAPER"))
    with pytest.raises(ClaimTierError, match="unknown"):
        validate_artifact_tier_labels(("PROFITABLE",))
