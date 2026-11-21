from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.options_risk import OptionLegQuote
from financial_brain.forward_edge.options_spread import (
    ExpiryScenario,
    OptionsSpreadError,
    evaluate_executable_spread,
)


def _leg(leg_id, side, strike, bid, ask, when, *, token=1):
    return OptionLegQuote(leg_id, side, token, f"ABC{strike}CE", "ABC", "CE", strike,
                          date(2026, 11, 26), 50, when, bid, ask, 20, 20)


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
