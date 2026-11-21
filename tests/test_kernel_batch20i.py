from __future__ import annotations

import datetime as dt

import pytest

from financial_brain.kernel.batch20i_ops import (
    api_doc,
    deterministic_example,
    exit_resolution,
    futures_breakout,
    is_half_day,
    market_overview,
    parse_earnings,
    reason_stack,
    reconcile,
    role_presets,
    screen_stocks,
    search_instruments,
    session_breakout,
    spread_spike_loss,
    top_k_watchlist,
    volume_heatmap,
)


def test_futures_breakout_uses_prior_bars_only() -> None:
    close = [1.0, 2.0, 3.0, 4.0, 1.0]
    assert futures_breakout(close, close, close, 2) == [0, 0, 1, 1, -1]


def test_reconciliation_lists_only_differences() -> None:
    assert reconcile({"NIFTY": 50, "ABC": 10}, {"NIFTY": 25, "ABC": 10, "XYZ": 5}) == {"NIFTY": 25, "XYZ": -5}


def test_session_breakout_and_spike_loss() -> None:
    assert session_breakout(105.0, 104.0, 100.0) == 1
    assert session_breakout(99.0, 104.0, 100.0) == -1
    assert spread_spike_loss(100, 0.5, 4.0) == pytest.approx(150.0)
    with pytest.raises(ValueError):
        spread_spike_loss(100, 0.5, 0.5)


def test_screen_keeps_only_stocks_meeting_all_minimums() -> None:
    stocks = [
        {"symbol": "A", "liquidity": 10, "pe": 12, "momentum": 0.1},
        {"symbol": "B", "liquidity": 1, "pe": 12, "momentum": 0.1},
        {"symbol": "C", "liquidity": 10, "pe": 40, "momentum": 0.1},
    ]
    assert screen_stocks(stocks, min_liquidity=5, max_pe=20, min_momentum=0.0) == ["A"]


def test_earnings_fixtures_parse_and_reject_bad_dates() -> None:
    assert parse_earnings(["INFY,2026-11-20"]) == {"INFY": dt.date(2026, 11, 20)}
    with pytest.raises(ValueError):
        parse_earnings(["INFY,2026-13-40"])


def test_volume_heatmap_sums_by_bucket_and_day() -> None:
    grid = volume_heatmap([(0, 1, 100.0), (0, 1, 50.0), (1, 1, 10.0), (0, 2, 5.0)])
    assert grid == {0: {1: 150.0, 2: 5.0}, 1: {1: 10.0}}


def test_reason_stack_is_ordered_and_refuses_empty() -> None:
    assert reason_stack(["breakout", "volume"]) == "1) breakout 2) volume"
    with pytest.raises(ValueError):
        reason_stack([])


def test_exit_resolution_fills_gap_through_stop_at_open() -> None:
    assert exit_resolution("LONG", 95.0, 110.0, open_=90.0, high=91.0, low=89.0) == ("STOP_GAP", 90.0)
    assert exit_resolution("LONG", 95.0, 110.0, open_=100.0, high=101.0, low=94.0) == ("STOP", 95.0)
    assert exit_resolution("LONG", 95.0, 110.0, open_=100.0, high=111.0, low=99.0) == ("TARGET", 110.0)
    assert exit_resolution("LONG", 95.0, 110.0, open_=100.0, high=105.0, low=99.0) is None


def test_watchlist_takes_top_k_with_name_tie_break() -> None:
    assert top_k_watchlist({"B": 2.0, "A": 2.0, "C": 1.0}, k=2) == ["A", "B"]


def test_half_day_flag_and_personas() -> None:
    assert is_half_day("2026-11-14", {"2026-11-14"}) == "HALF_DAY"
    assert is_half_day("2026-11-09", {"2026-11-14"}) == "NORMAL"
    assert "paper" in role_presets("options_trader")
    with pytest.raises(KeyError):
        role_presets("hedge_fund_manager")


def test_instrument_search_prefers_exact_and_flags_ambiguity() -> None:
    assert search_instruments(["NIFTY", "NIFTYBANK"], "nifty")["exact"] == "NIFTY"
    out = search_instruments(["NIFTYBANK", "NIFTYIT"], "nifty")
    assert out["exact"] is None and out["ambiguous"] is True


def test_market_overview_ranks_by_change() -> None:
    assert market_overview({"A": -1.0, "B": 2.0, "C": 0.5}) == [("B", 2.0), ("C", 0.5), ("A", -1.0)]


def test_api_doc_has_a_section_per_route_and_needs_examples() -> None:
    doc = api_doc({"/v1/bars": {"symbol": "NIFTY", "limit": 10}})
    assert doc.startswith("## /v1/bars")
    assert "symbol: str = 'NIFTY'" in doc
    with pytest.raises(ValueError):
        api_doc({"/v1/bars": {}})


def test_seeded_example_is_reproducible() -> None:
    assert deterministic_example(3) == deterministic_example(3)
    assert deterministic_example(3) != deterministic_example(4)
