from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.signals.mf_allocation import (MFAction, MFAllocationRecommendation,
                                                   NavStatus)

NOW = datetime(2026, 11, 23, 10, tzinfo=timezone.utc)


def recommendation(**changes):
    values = {"recommendation_id": "r1", "scheme_code": "135762", "action": MFAction.REDEEM,
              "amount_inr": 10_000, "decision_at": NOW,
              "transaction_cutoff_at": NOW + timedelta(hours=2),
              "benchmark_id": "NIFTY_500_TRI", "nav_status": NavStatus.UNCONFIRMED,
              "estimated_transaction_fee_inr": 0, "estimated_exit_load_inr": None,
              "cost_evidence_status": "PARTIAL"}
    return MFAllocationRecommendation(**(values | changes))


def test_recommendation_discloses_cutoff_unconfirmed_nav_and_missing_exit_load():
    item = recommendation()
    assert item.workflow_type == "ALLOCATION_RECOMMENDATION"
    assert item.nav_status == NavStatus.UNCONFIRMED
    assert not item.cost_estimate_complete


def test_recommendation_rejects_instant_or_unbounded_cost_representation():
    with pytest.raises(ValueError, match="cutoff"):
        recommendation(transaction_cutoff_at=NOW - timedelta(seconds=1))
    with pytest.raises(ValueError, match="fees"):
        recommendation(estimated_transaction_fee_inr=-1)
