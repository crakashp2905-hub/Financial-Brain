from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.paper.options import Quote, OptionPaperError, cost_hurdle, long_round_trip


IST = timezone(timedelta(hours=5, minutes=30))


def quote(*, hour=10, bid=100.0, ask=101.0, token=123, expiry=date(2026, 11, 5),
          bid_qty=1000, ask_qty=1000):
    return Quote(token, "NIFTY26NOV25000CE", expiry, 65,
                 datetime(2026, 10, 29, hour, tzinfo=IST), bid, ask, bid_qty, ask_qty)


def test_long_option_uses_ask_then_bid_and_option_charges():
    result = long_round_trip(quote(), quote(hour=11, bid=106, ask=107))
    assert result["quantity"] == 65
    assert result["gross_pnl_inr"] == 5 * 65
    assert 0 < result["net_pnl_inr"] < result["gross_pnl_inr"]
    assert result["settlement_modelled"] is False


def test_missing_liquidity_or_contract_mismatch_cannot_be_paper_filled():
    with pytest.raises(OptionPaperError, match="quantity"):
        long_round_trip(quote(), quote(hour=11, bid_qty=10))
    with pytest.raises(OptionPaperError, match="same contract"):
        long_round_trip(quote(), quote(hour=11, token=999))
    with pytest.raises(OptionPaperError, match="before expiry"):
        long_round_trip(quote(expiry=date(2026, 10, 29)), quote(hour=11, expiry=date(2026, 10, 29)))


def test_option_cost_hurdle_requires_executable_reward():
    assert not cost_hurdle(quote(), lots=1, expected_exit_bid=101.01)["passes"]
    assert cost_hurdle(quote(), lots=1, expected_exit_bid=110)["passes"]
