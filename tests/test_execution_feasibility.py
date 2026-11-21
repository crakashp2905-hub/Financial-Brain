from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.execution_feasibility import (
    ExecutionEvidence,
    FeasibilityError,
    FeasibilityState,
    assess_execution_feasibility,
)


def _evidence(**changes):
    now = datetime(2026, 11, 21, 10, tzinfo=timezone.utc)
    values = {
        "bid": Decimal("99.95"), "ask": Decimal("100.00"),
        "quote_age_ms": 50, "max_quote_age_ms": 500,
        "spread_bps": Decimal("5"), "max_spread_bps": Decimal("10"),
        "estimated_slippage_bps": Decimal("2"), "max_slippage_bps": Decimal("8"),
        "visible_quantity": Decimal("20"), "required_quantity": Decimal("10"),
        "quote_source_id": "kite:NSE", "quote_source_hash": "a" * 64,
        "quote_event_at": now - timedelta(milliseconds=100),
        "quote_received_at": now - timedelta(milliseconds=50), "observed_at": now,
    }
    return ExecutionEvidence(**(values | changes))


def test_actionable_requires_fresh_liquid_two_sided_quote_and_cost_limits():
    assert assess_execution_feasibility(_evidence()).state is FeasibilityState.ACTIONABLE


@pytest.mark.parametrize(("change", "reason"), [
    ({"bid": None}, "no_viable_two_sided_quote"),
    ({"quote_age_ms": None}, "quote_age_unknown"),
    ({"quote_age_ms": 501}, "quote_stale"),
    ({"spread_bps": Decimal("11")}, "spread_exceeds_limit"),
    ({"estimated_slippage_bps": Decimal("9")}, "slippage_exceeds_limit"),
    ({"visible_quantity": Decimal("9")}, "insufficient_visible_depth"),
    ({"option_spread_bps": Decimal("16"), "max_option_spread_bps": Decimal("15")}, "option_spread_exceeds_limit"),
    ({"redemption_allowed": False}, "fund_redemption_blocked"),
    ({"market_halted": True}, "market_halted"),
])
def test_infeasible_evidence_fails_closed_to_watch_or_no_trade(change, reason):
    decision = assess_execution_feasibility(_evidence(**change))
    assert decision.state in {FeasibilityState.WATCH, FeasibilityState.NO_TRADE}
    assert reason in decision.reasons


def test_unknown_quote_quality_is_watch_and_never_actionable():
    decision = assess_execution_feasibility(_evidence(spread_bps=None))
    assert decision.state is FeasibilityState.WATCH
    assert "spread_unknown" in decision.reasons


@pytest.mark.parametrize("change, reason", [
    ({"ltp_only": True}, "ltp_only_non_executable"),
    ({"quote_source_id": None}, "quote_source_provenance_missing"),
    ({"quote_received_at": None}, "quote_event_receipt_or_observation_clock_missing"),
    ({"quote_received_at": datetime(2026, 11, 21, 10, tzinfo=timezone.utc) + timedelta(seconds=1)},
     "quote_received_in_future"),
    ({"quote_source_hash": "bad"}, "SHA-256"),
])
def test_feed_provenance_clock_and_ltp_requirements(change, reason):
    if reason == "SHA-256":
        with pytest.raises(FeasibilityError, match=reason):
            _evidence(**change)
        return
    decision = assess_execution_feasibility(_evidence(**change))
    assert decision.state is FeasibilityState.WATCH
    assert reason in decision.reasons


def test_invalid_option_spread_pair_is_rejected():
    with pytest.raises(FeasibilityError, match="supplied together"):
        _evidence(option_spread_bps=Decimal("10"))
