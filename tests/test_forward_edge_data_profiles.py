from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.data_profiles import (
    DataProfileError,
    DataProfileKind,
    InstrumentDataProfile,
    profiles_from_constraints,
    validate_profile_event,
)
from financial_brain.forward_edge.market_events import EventKind, MarketEvent

T0 = datetime(2026, 11, 13, 4, 45, tzinfo=timezone.utc)


def _event(kind, instrument="NSE:INFY", payload=None):
    return MarketEvent(
        f"event-{kind.value}-{instrument}", kind, instrument.split(":", 1)[0], instrument,
        T0, T0 + timedelta(milliseconds=1), T0 + timedelta(milliseconds=2),
        payload or {}, "FIXTURE")


def _executable_quote(instrument="NSE:INFY"):
    return _event(EventKind.QUOTE, instrument, {
        "bid": 100.0, "ask": 100.1, "bid_quantity": 10, "ask_quantity": 12,
        "volume": 100,
        "depth": {"buy": [{"price": 100.0, "quantity": 10}],
                  "sell": [{"price": 100.1, "quantity": 12}]},
    })


def test_profile_map_requires_exactly_one_profile_per_frozen_instrument():
    universe = ("NSE:NIFTY50", "NSE:INFY")
    constraints = {"data_profiles": [
        {"instrument_id": "NSE:NIFTY50", "profile": "INDEX_LEVEL_ONLY"},
        {"instrument_id": "NSE:INFY", "profile": "EQUITY_EXECUTION"},
    ]}
    profiles = profiles_from_constraints(constraints, universe)
    assert profiles["NSE:NIFTY50"].kind is DataProfileKind.INDEX_LEVEL_ONLY
    assert profiles["NSE:INFY"].kind is DataProfileKind.EQUITY_EXECUTION
    assert profiles_from_constraints({}, universe) is None
    with pytest.raises(DataProfileError, match="exactly cover"):
        profiles_from_constraints({"data_profiles": constraints["data_profiles"][:1]}, universe)
    with pytest.raises(DataProfileError, match="duplicate"):
        profiles_from_constraints({"data_profiles": constraints["data_profiles"] +
                                   [constraints["data_profiles"][0]]}, universe)


def test_profiles_distinguish_analysis_levels_from_executable_quotes():
    index = InstrumentDataProfile("NSE:NIFTY50", DataProfileKind.INDEX_LEVEL_ONLY)
    validate_profile_event(index, _event(EventKind.TICK, "NSE:NIFTY50", {"price": 25000.0}))
    with pytest.raises(DataProfileError, match="cannot supply execution"):
        validate_profile_event(index, _event(EventKind.TICK, "NSE:NIFTY50", {"price": 25000.0}),
                               for_execution=True)

    equity = InstrumentDataProfile("NSE:INFY", DataProfileKind.EQUITY_EXECUTION)
    validate_profile_event(equity, _event(EventKind.TICK, payload={"price": 100.0}))
    with pytest.raises(DataProfileError, match="equity bid/ask and depth"):
        validate_profile_event(equity, _event(EventKind.QUOTE, payload={"bid": 100, "ask": 100.1}),
                               for_execution=True)
    validate_profile_event(equity, _executable_quote(), for_execution=True)


def test_options_and_mutual_funds_require_product_specific_data():
    option = InstrumentDataProfile("NFO:INFY26NOV1500CE", DataProfileKind.OPTION_EXECUTION)
    validate_profile_event(option, _executable_quote("NFO:INFY26NOV1500CE"), for_execution=True)
    with pytest.raises(DataProfileError, match="option bid/ask and depth"):
        validate_profile_event(option, _event(EventKind.QUOTE, "NFO:INFY26NOV1500CE",
                                              {"bid": 1.0, "ask": 1.2}), for_execution=True)

    fund = InstrumentDataProfile("MF:SCHEME-1", DataProfileKind.MUTUAL_FUND_NAV)
    nav = _event(EventKind.NAV, "MF:SCHEME-1", {"nav_per_unit": 125.4, "nav_date": "2026-11-12"})
    validate_profile_event(fund, nav)
    with pytest.raises(DataProfileError, match="requires an NAV observation"):
        validate_profile_event(fund, _event(EventKind.TICK, "MF:SCHEME-1", {"price": 125.4}))
    with pytest.raises(DataProfileError, match="not quote execution"):
        validate_profile_event(fund, nav, for_execution=True)
