"""FE-0062 executable option leg quotes, legging loss and honest margin/settlement states."""
from datetime import date, datetime, timezone

import pytest

from financial_brain.forward_edge.options_risk import OptionLegQuote, OptionsRiskError, evaluate_defined_risk_legs

NOW = datetime(2026, 11, 15, 4, 0, tzinfo=timezone.utc)


def _legs():
    return (OptionLegQuote("long", "BUY", 1, "NIFTY26NOV25000CE", "NIFTY", "CE", 25000,
                           date(2026, 11, 26), 75, NOW, 100, 101, 1000, 1000),
            OptionLegQuote("short", "SELL", 2, "NIFTY26NOV25100CE", "NIFTY", "CE", 25100,
                           date(2026, 11, 26), 75, NOW, 60, 61, 1000, 1000))


def test_executable_multileg_quotes_report_legging_and_unverified_margin() -> None:
    report = evaluate_defined_risk_legs(legs=_legs(), quantity=75, as_of=NOW,
                                        leg_order=("long", "short"), max_quote_age_seconds=30)
    assert report["entry_debit_inr"] == pytest.approx(3075)
    assert report["economic_max_loss_inr"] == pytest.approx(3075)
    assert report["legging_loss_bound_inr"] == pytest.approx(7575)
    assert report["margin_status"] == "NOT_VERIFIED"
    assert report["expiry_settlement_status"] == "NOT_MODELLED"


def test_put_debit_vertical_uses_long_higher_strike_and_short_lower_strike() -> None:
    legs = (OptionLegQuote("long", "BUY", 11, "NIFTY26NOV25100PE", "NIFTY", "PE", 25100,
                           date(2026, 11, 26), 75, NOW, 104, 105, 1000, 1000),
            OptionLegQuote("short", "SELL", 12, "NIFTY26NOV25000PE", "NIFTY", "PE", 25000,
                           date(2026, 11, 26), 75, NOW, 59, 60, 1000, 1000))
    report = evaluate_defined_risk_legs(legs=legs, quantity=75, as_of=NOW,
                                        leg_order=("long", "short"), max_quote_age_seconds=30)
    assert report["entry_debit_inr"] == pytest.approx(3450)
    assert report["economic_max_loss_inr"] == pytest.approx(3450)


@pytest.mark.parametrize("option_type,long_strike,short_strike", [("CE", 25100, 25000),
                                                                     ("PE", 25000, 25100)])
def test_inverted_debit_verticals_fail_closed(option_type, long_strike, short_strike) -> None:
    legs = (OptionLegQuote("long", "BUY", 21, "long", "NIFTY", option_type, long_strike,
                           date(2026, 11, 26), 75, NOW, 100, 101, 1000, 1000),
            OptionLegQuote("short", "SELL", 22, "short", "NIFTY", option_type, short_strike,
                           date(2026, 11, 26), 75, NOW, 60, 61, 1000, 1000))
    with pytest.raises(OptionsRiskError, match="inverted"):
        evaluate_defined_risk_legs(legs=legs, quantity=75, as_of=NOW,
                                   leg_order=("long", "short"), max_quote_age_seconds=30)


def test_expiry_intrinsic_is_only_a_scenario_and_verified_margin_is_explicit() -> None:
    report = evaluate_defined_risk_legs(legs=_legs(), quantity=75, as_of=NOW,
                                        leg_order=("long", "short"), max_quote_age_seconds=30,
                                        underlying_at_expiry=25050, verified_margin_inr=5000)
    assert report["expiry_settlement_status"] == "INTRINSIC_SCENARIO_ONLY"
    assert report["expiry_intrinsic_scenario_pnl_inr"] == pytest.approx(675)
    assert report["margin_status"] == "BROKER_VERIFIED"


@pytest.mark.parametrize("change", ["stale", "thin", "mid_only", "invalid_order"])
def test_non_executable_or_stale_leg_quotes_fail_closed(change) -> None:
    legs = list(_legs())
    order = ("long", "short")
    as_of = NOW
    if change == "stale":
        legs[1] = OptionLegQuote(**{**legs[1].__dict__, "observed_at": NOW.replace(hour=2)})
    elif change == "thin":
        legs[1] = OptionLegQuote(**{**legs[1].__dict__, "bid_qty": 1})
    elif change == "mid_only":
        legs[1] = OptionLegQuote(**{**legs[1].__dict__, "ask": 0})
    else:
        order = ("long", "long")
    with pytest.raises(OptionsRiskError):
        evaluate_defined_risk_legs(legs=tuple(legs), quantity=75, as_of=as_of,
                                   leg_order=order, max_quote_age_seconds=30)
