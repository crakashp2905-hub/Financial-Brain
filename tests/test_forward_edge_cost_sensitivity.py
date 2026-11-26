"""FE-0061 cost stress grid and quote-observed execution floor."""
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.costs.india import Segment
from financial_brain.forward_edge.cost_sensitivity import SensitivityError, cost_sensitivity
from financial_brain.forward_edge.market_events import EventKind, MarketEvent

NOW = datetime(2026, 11, 26, 5, 0, tzinfo=timezone.utc)


def quote(*, available_at=NOW, asks=None, bids=None):
    asks = asks or [[100.2, 10], [100.4, 20]]
    bids = bids or [[99.8, 10], [99.6, 20]]
    return MarketEvent(
        event_id="quote-1", kind=EventKind.QUOTE, venue="NSE", instrument_id="ABC",
        event_time=available_at, received_at=available_at, available_at=available_at,
        payload={"bid": 99.8, "ask": 100.2, "bid_levels": bids, "ask_levels": asks},
        source="kite:observed", evidence_id="session-1",
    )


def report(**kwargs):
    defaults = dict(gross_pnl=1000, order_value=1000, base_slippage=10,
                    base_impact=5, calibration_source="provisional:bucket-model",
                    segment=Segment.INTRADAY, bucket="large")
    defaults.update(kwargs)
    return cost_sensitivity(**defaults)


def test_sensitivity_builds_independent_cost_and_slippage_grid() -> None:
    result = report()
    assert len(result.scenarios) == 16
    assert {(row["cost_multiplier"], row["slippage_multiplier"]) for row in result.scenarios} == {
        (cost, slippage) for cost in (1, 1.25, 1.5, 2) for slippage in (1, 1.25, 1.5, 2)
    }
    cost_only = next(row for row in result.scenarios
                     if row["cost_multiplier"] == 1.25 and row["slippage_multiplier"] == 1)
    slip_only = next(row for row in result.scenarios
                     if row["cost_multiplier"] == 1 and row["slippage_multiplier"] == 1.25)
    base = result.scenarios[0]
    assert cost_only["slippage"] == base["slippage"]
    assert cost_only["fees"] > base["fees"]
    assert slip_only["fees"] == base["fees"]
    assert slip_only["slippage"] > base["slippage"]
    assert result.calibrated is False


def test_quote_floor_uses_spread_depth_fees_and_quote_hash() -> None:
    observed = quote()
    result = report(gross_pnl=1000, order_value=1000, base_slippage=10, base_impact=0,
                    calibration_source="kite:observed", observed_quote=observed, quantity=10,
                    as_of=NOW)
    assert result.calibrated is True
    assert result.observed_quote_sha256 == observed.sha256()
    # Midpoint notional is 1000. Spread costs 4 INR; walking 10 units uses only top level.
    assert result.observed_spread_and_depth_inr == pytest.approx(4)
    assert result.observed_cost_floor_inr == pytest.approx(result.statutory_and_broker_fees + 4)


@pytest.mark.parametrize("offset", [timedelta(seconds=6), timedelta(seconds=1)])
def test_stale_or_future_quote_rejected(offset) -> None:
    with pytest.raises(SensitivityError):
        report(observed_quote=quote(available_at=NOW + offset), quantity=10, as_of=NOW,
               calibration_source="kite:observed")


def test_quote_floor_rejects_mismatched_notional_and_insufficient_depth() -> None:
    with pytest.raises(SensitivityError, match="order_value"):
        report(order_value=1100, observed_quote=quote(), quantity=10, as_of=NOW,
               calibration_source="kite:observed")
    thin = quote(asks=[[100.2, 5]], bids=[[99.8, 5]])
    with pytest.raises(SensitivityError, match="depth"):
        report(observed_quote=thin, quantity=10, as_of=NOW,
               calibration_source="kite:observed")


def test_depth_must_be_best_to_worst_and_uncalibrated_without_quote() -> None:
    bad = quote(asks=[[100.4, 10], [100.2, 20]])
    with pytest.raises(SensitivityError, match="ordered"):
        report(observed_quote=bad, quantity=10, as_of=NOW,
               calibration_source="kite:observed")
    assert report(calibration_source="observed:but-no-quote").calibrated is False


@pytest.mark.parametrize("kwargs", [
    {"base_slippage": None, "base_impact": 1},
    {"base_slippage": -1, "base_impact": 1},
    {"base_slippage": 1, "base_impact": 1, "calibration_source": None},
])
def test_missing_or_invalid_estimates_fail_closed(kwargs) -> None:
    with pytest.raises(SensitivityError):
        report(**kwargs)
