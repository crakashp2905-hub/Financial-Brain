from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.paper.options import OptionPaperError, Quote
from financial_brain.paper.options_spread import Leg, debit_vertical


IST = timezone(timedelta(hours=5, minutes=30))


def leg(token, strike, bid, ask, *, kind="CE", expiry=date(2026, 11, 5), qty=1000):
    q = Quote(token, f"NIFTY-{strike}-{kind}", expiry, 65,
              datetime(2026, 10, 29, 10, tzinfo=IST), bid, ask, qty, qty)
    return Leg(q, "NIFTY", kind, strike)


def test_call_debit_vertical_has_bounded_economic_loss():
    result = debit_vertical(leg(1, 25000, 100, 101), leg(2, 25100, 40, 41))
    assert result["entry_debit_inr"] == 61 * 65
    assert result["max_loss_before_exit_charges_inr"] == result["economic_risk_reserve_inr"]
    assert result["max_gain_before_exit_charges_inr"] > 0
    assert result["broker_margin_verified"] is False


def test_put_debit_vertical_and_invalid_leg_pair():
    result = debit_vertical(leg(3, 25100, 100, 101, kind="PE"),
                            leg(4, 25000, 40, 41, kind="PE"))
    assert result["option_type"] == "PE"
    with pytest.raises(OptionPaperError, match="same underlying"):
        debit_vertical(leg(1, 25000, 100, 101), leg(2, 25100, 40, 41, kind="PE"))
    with pytest.raises(OptionPaperError, match="quantity"):
        debit_vertical(leg(1, 25000, 100, 101, qty=1), leg(2, 25100, 40, 41))
