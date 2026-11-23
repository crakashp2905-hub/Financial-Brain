from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.mf_allocation_recommendation import (
    MFAction, MFAllocationRecommendation, MFRecommendationError, NAVStatus,
)


def _recommendation(**changes):
    now = datetime(2026, 11, 21, 10, tzinfo=timezone.utc)
    values = {
        "recommendation_id": "550e8400-e29b-41d4-a716-446655440000",
        "scheme_code": "123456", "plan": "DIRECT", "option": "GROWTH",
        "action": MFAction.ALLOCATE, "decision_at": now,
        "effective_date": date(2026, 11, 21), "cutoff_at": now + timedelta(hours=2),
        "amount_inr": Decimal("10000"), "lot_fee_estimate_inr": Decimal("12.50"),
        "benchmark_id": "NIFTY_50_TRI@2026-10", "nav_status": NAVStatus.UNCONFIRMED_PENDING,
        "nav_as_of": None, "nav_published_at": None, "nav_available_at": None,
        "horizon_sessions": 60, "rationale": "rebalance to target allocation",
    }
    return MFAllocationRecommendation(**(values | changes))


def test_recommendation_is_guidance_with_cutoff_cost_benchmark_and_nav_state():
    recommendation = _recommendation()
    assert recommendation.is_instant_execution is False
    assert recommendation.nav_status is NAVStatus.UNCONFIRMED_PENDING
    assert recommendation.cutoff_at > recommendation.decision_at
    with pytest.raises(FrozenInstanceError):
        recommendation.amount_inr = Decimal("1")


def test_confirmed_nav_must_have_causal_publication_and_availability_evidence():
    now = datetime(2026, 11, 21, 10, tzinfo=timezone.utc)
    confirmed = _recommendation(
        nav_status=NAVStatus.CONFIRMED_PUBLISHED, nav_as_of=date(2026, 11, 20),
        nav_published_at=now - timedelta(minutes=5), nav_available_at=now - timedelta(minutes=4),
    )
    assert confirmed.nav_as_of == date(2026, 11, 20)
    with pytest.raises(MFRecommendationError, match="future NAV availability"):
        _recommendation(
            nav_status=NAVStatus.CONFIRMED_PUBLISHED, nav_as_of=date(2026, 11, 21),
            nav_published_at=now, nav_available_at=now + timedelta(minutes=1),
        )


def test_unconfirmed_nav_cannot_carry_claimed_publication_metadata():
    with pytest.raises(MFRecommendationError, match="cannot include fabricated"):
        _recommendation(nav_as_of=date(2026, 11, 21))


@pytest.mark.parametrize("changes, message", [
    ({"cutoff_at": datetime(2026, 11, 21, 9, tzinfo=timezone.utc)}, "cutoff_at"),
    ({"amount_inr": Decimal("0")}, "amount_inr"),
    ({"lot_fee_estimate_inr": Decimal("-1")}, "lot_fee_estimate_inr"),
    ({"horizon_sessions": 0}, "horizon_sessions"),
])
def test_invalid_recommendation_fields_fail(changes, message):
    with pytest.raises(MFRecommendationError, match=message):
        _recommendation(**changes)
