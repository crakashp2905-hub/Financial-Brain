from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.options_risk import OptionLegQuote
from financial_brain.forward_edge.options_spread import (
    ExpiryScenario,
    OptionMarketStress,
    OptionsSpreadError,
    evaluate_executable_spread,
    stress_option_market,
)


def _leg(leg_id, side, strike, bid, ask, when, *, token=1):
    return OptionLegQuote(leg_id, side, token, f"ABC{strike}CE", "ABC", "CE", strike,
                          date(2026, 11, 26), 50, when, bid, ask, 20, 20,
                          underlying_price=100, implied_volatility=0.30)


def test_vertical_spread_uses_entry_and_exit_bid_ask_and_fees() -> None:
    entry_time = datetime(2026, 11, 16, 5, tzinfo=timezone.utc)
    exit_time = entry_time + timedelta(hours=4)
    entry = (_leg("long", "BUY", 100, 9, 10, entry_time),
             _leg("short", "SELL", 110, 4, 5, entry_time, token=2))
    exit_legs = (_leg("long", "SELL", 100, 12, 13, exit_time),
                 _leg("short", "BUY", 110, 1, 2, exit_time, token=2))
    report = evaluate_executable_spread(entry_legs=entry, exit_legs=exit_legs, lots=1,
                                        entry_as_of=entry_time, exit_as_of=exit_time,
                                        max_quote_age_seconds=300, fees_inr=20,
                                        settlement_mode="CASH_SETTLED",
                                        expiry_scenarios=(ExpiryScenario("above-both", 120),))
    assert report["entry_cash_flow_inr"] == -300
    assert report["exit_cash_flow_inr"] == 500
    assert report["net_pnl_inr"] == 180
    scenario = report["expiry_scenarios"][0]
    assert scenario["net_expiry_pnl_after_entry_cash_and_fees"] == 180
    assert scenario["assignment_status"] == "SCENARIO_ONLY"


def test_physical_expiry_reports_gross_assignment_cash_and_shares() -> None:
    when = datetime(2026, 11, 16, 5, tzinfo=timezone.utc)
    entry = (_leg("long", "BUY", 100, 9, 10, when),
             _leg("short", "SELL", 110, 4, 5, when, token=2))
    report = evaluate_executable_spread(entry_legs=entry, exit_legs=(
        _leg("long", "SELL", 100, 9, 10, when), _leg("short", "BUY", 110, 4, 5, when, token=2)),
        lots=1, entry_as_of=when, exit_as_of=when, max_quote_age_seconds=300, fees_inr=0,
        settlement_mode="PHYSICAL_DELIVERY", expiry_scenarios=(ExpiryScenario("above-both", 120),))
    scenario = report["expiry_scenarios"][0]
    assert scenario["physical_assignment_cash_required_inr"] == 5000
    assert scenario["physical_assignment_cash_received_inr"] == 5500
    assert scenario["physical_shares_received"] == 50
    assert scenario["physical_shares_delivered"] == 50


def test_mid_only_stale_or_nonmatching_exit_data_is_rejected() -> None:
    when = datetime(2026, 11, 16, 5, tzinfo=timezone.utc)
    old = when - timedelta(minutes=10)
    entry = (_leg("one", "BUY", 100, 9, 10, when),)
    valid_exit = (_leg("one", "SELL", 100, 9, 10, when),)
    with pytest.raises(OptionsSpreadError, match="stale"):
        evaluate_executable_spread(entry_legs=(_leg("one", "BUY", 100, 9, 10, old),),
                                   exit_legs=valid_exit, lots=1, entry_as_of=when,
                                   exit_as_of=when, max_quote_age_seconds=60, fees_inr=0,
                                   settlement_mode="CASH_SETTLED", expiry_scenarios=())
    with pytest.raises(OptionsSpreadError, match="close"):
        evaluate_executable_spread(entry_legs=entry, exit_legs=(_leg("one", "BUY", 100, 9, 10, when),),
                                   lots=1, entry_as_of=when, exit_as_of=when,
                                   max_quote_age_seconds=300, fees_inr=0,
                                   settlement_mode="CASH_SETTLED", expiry_scenarios=())


def test_expiry_and_news_stress_combines_spread_iv_and_liquidity_shocks() -> None:
    when = datetime(2026, 11, 25, 5, tzinfo=timezone.utc)
    legs = (_leg("near-atm", "BUY", 100, 4.9, 5.1, when),)
    stress = stress_option_market(
        legs=legs, lots=2, as_of=when, fees_inr=5, minimum_liquidity_contracts=6,
        scenarios=(
            OptionMarketStress("expiry-crush", 30, 0, -0.20, 3.0, 0.25),
            OptionMarketStress("news-gap", 60, -0.02, 0.10, 2.0, 1.0),
        ),
    )
    assert stress["calibrated"] is False
    assert len(stress["report_hash"]) == 64
    expiry, news = stress["scenarios"]
    assert expiry["near_atm"] is True
    assert expiry["iv_change"] == -0.20
    assert expiry["exit_available"] is False
    assert expiry["estimated_net_pnl_inr"] is None
    assert news["exit_available"] is True
    assert news["evidence_tier"] == "UNCALIBRATED_SCENARIO"
    assert len(news["leg_marks"]) == 1
    assert news["leg_marks"][0]["shocked_ask"] >= news["leg_marks"][0]["shocked_bid"]


def test_market_stress_applies_skew_shift_by_strike_moneyness_and_reports_marks() -> None:
    when = datetime(2026, 11, 25, 5, tzinfo=timezone.utc)
    leg = _leg("otm-call", "BUY", 110, 2.9, 3.1, when)
    result = stress_option_market(
        legs=(leg,), lots=1, as_of=when, fees_inr=0,
        scenarios=(OptionMarketStress("skew-up", 60, 0, 0, 1, 1, 0.01),),
    )
    mark = result["scenarios"][0]["leg_marks"][0]
    assert mark["implied_volatility"] == pytest.approx(0.40)
    assert mark["underlying_price"] == pytest.approx(100)


def test_market_stress_rejects_missing_spot_or_iv_and_invalid_shocks() -> None:
    when = datetime(2026, 11, 25, 5, tzinfo=timezone.utc)
    leg = _leg("one", "BUY", 100, 4.9, 5.1, when)
    scenario = OptionMarketStress("shock", 30, 0, -0.1, 2, 0.5)
    missing = OptionLegQuote(leg.leg_id, leg.side, leg.token, leg.symbol, leg.underlying,
                             leg.option_type, leg.strike, leg.expiry, leg.lot_size, leg.observed_at,
                             leg.bid, leg.ask, leg.bid_qty, leg.ask_qty)
    with pytest.raises(OptionsSpreadError, match="spot and implied volatility"):
        stress_option_market(legs=(missing,), lots=1, as_of=when, fees_inr=0, scenarios=(scenario,))
    with pytest.raises(OptionsSpreadError, match="stress dimensions"):
        OptionMarketStress("bad", 30, 0, 0, 0.5, 1)
