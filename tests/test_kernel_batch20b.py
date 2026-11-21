from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.batch20b_market import (
    book_apply,
    normalise_broker,
    order_events,
    replay_granularity,
    route,
    run_stages,
    session_timers,
    sync_skew,
)
from financial_brain.kernel.batch20b_orders import (
    decision_record,
    failover_plan,
    negotiate,
    reason_counts,
    ticket_missing,
)
from financial_brain.kernel.batch20b_research import (
    Contract,
    beta_and_residuals,
    browse_contracts,
    drawdown_contribution,
    etf_fields,
    pearson,
    split_adjusted_returns,
    usable_surprise,
)


def test_events_sort_per_instrument_then_time_then_kind() -> None:
    events = [("X", 10, 2, "TRADE"), ("X", 10, 1, "QUOTE"), ("A", 9, 5, "BAR")]
    assert order_events(events) == [("A", 9, 5, "BAR"), ("X", 10, 1, "QUOTE"), ("X", 10, 2, "TRADE")]
    with pytest.raises(ValueError):
        order_events([("X", 1, 1, "MYSTERY")])


def test_routing_uses_wildcards_and_exact_matches() -> None:
    subs = {"s1": {"venue": "NSE", "symbol": None}, "s2": {"venue": "BSE"}}
    assert route(subs, {"venue": "NSE", "symbol": "ABC"}) == ["s1"]


def test_stage_failure_does_not_stop_other_stages() -> None:
    out = run_stages([("ok", lambda x: x + 1), ("bad", lambda x: 1 / 0)], 1)
    assert out["ok"] == ("ok", 2)
    assert out["bad"] == ("error", "ZeroDivisionError: division by zero")


def test_session_timers_include_open_and_close() -> None:
    times = session_timers(555, 930, 60)
    assert times[0] == 555 and times[-1] == 930
    with pytest.raises(ValueError):
        session_timers(930, 555, 60)


def test_leg_clock_skew_within_tolerance() -> None:
    assert sync_skew({"a": 100, "b": 130}, tolerance_ms=50) == (True, 30)
    assert sync_skew({"a": 100, "b": 130}, tolerance_ms=20) == (False, 30)


def test_replay_keeps_only_the_granularity_kinds() -> None:
    events = [(3, "BAR"), (1, "QUOTE"), (2, "TRADE")]
    assert replay_granularity(events, "QUOTE") == [(1, "QUOTE")]
    assert replay_granularity(events, "TICK") == [(1, "QUOTE"), (2, "TRADE")]


def test_book_updates_remove_zero_levels_and_refuse_negative() -> None:
    book = book_apply({100.0: 5, 101.0: 3}, [(100.0, 0), (102.0, 4)])
    assert book == {101.0: 3, 102.0: 4}
    with pytest.raises(ValueError):
        book_apply({}, [(1.0, -1)])


def test_broker_codes_are_normalised_and_unknown_refused() -> None:
    assert normalise_broker("F") == "FILL"
    with pytest.raises(KeyError):
        normalise_broker("Z")


def test_ticket_reports_missing_fields_for_the_asset_class() -> None:
    assert ticket_missing("FUTURE", {"symbol": "NIFTY", "quantity": 50, "side": "BUY"}) == ["expiry"]


def test_venue_must_support_every_required_capability() -> None:
    assert negotiate({"LIMIT", "MARKET"}, {"LIMIT", "BRACKET"}) == (False, {"BRACKET"})


def test_partial_multileg_failover_cancels_then_unwinds_in_reverse() -> None:
    plan = failover_plan(["A", "B", "C"], {"A": True, "B": False, "C": True})
    assert plan == ["CANCEL B", "UNWIND C", "UNWIND A"]
    assert failover_plan(["A", "B"], {"A": True, "B": True}) == []
    assert failover_plan(["A", "B"], {}) == ["CANCEL_ALL"]


def test_decision_record_needs_alternatives() -> None:
    record = decision_record("ABC", "limit", [("market", "spread too wide")])
    assert record["alternatives"] == [{"name": "market", "rejected_because": "spread too wide"}]
    with pytest.raises(ValueError):
        decision_record("ABC", "limit", [])


def test_reason_codes_are_counted_and_unknown_refused() -> None:
    assert reason_counts(["NO_MARGIN", "NO_MARGIN", "LOT_SIZE"]) == {"NO_MARGIN": 2, "LOT_SIZE": 1}
    with pytest.raises(ValueError):
        reason_counts(["MYSTERY"])


def test_contract_browser_filters_by_symbol_and_expiry() -> None:
    contracts = [Contract("NIFTY", "2026-11-26", "INR"), Contract("NIFTY", "2026-12-31", "INR"), Contract("USDINR", "2026-11-26", "INR")]
    assert len(browse_contracts(contracts, symbol="NIFTY", expiry="2026-11-26")) == 1


def test_pearson_perfect_and_inverse() -> None:
    assert pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)
    assert pearson([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == pytest.approx(-1.0)


def test_split_does_not_look_like_a_loss() -> None:
    assert split_adjusted_returns([100.0, 50.0, 55.0], {1: 2.0}) == pytest.approx([0.0, 0.1])


def test_beta_recovers_a_scaled_index_with_zero_residuals() -> None:
    index = [1.0, 2.0, 3.0, 4.0]
    beta, residuals = beta_and_residuals([2.0 * i for i in index], index)
    assert beta == pytest.approx(2.0)
    assert all(abs(r) < 1e-9 for r in residuals)


def test_etf_fields_and_expense_bounds() -> None:
    fields = etf_fields([0.01, 0.02], [0.0, 0.0], Decimal("0.001"))
    assert fields["tracking_error"] == pytest.approx(0.005)
    assert fields["expense_ratio"] == Decimal("0.001")
    with pytest.raises(ValueError):
        etf_fields([0.01, 0.02], [0.0, 0.0], Decimal("1"))


def test_drawdown_triggers_contribution_only_past_threshold() -> None:
    assert drawdown_contribution(Decimal("-0.12"), Decimal("0.10"), Decimal("500")) == Decimal("500")
    assert drawdown_contribution(Decimal("-0.05"), Decimal("0.10"), Decimal("500")) == Decimal("0")


def test_surprise_only_when_consensus_was_known_first() -> None:
    assert usable_surprise(Decimal("1.2"), Decimal("1.0"), consensus_at=5, event_at=10) == Decimal("0.2")
    assert usable_surprise(Decimal("1.2"), Decimal("1.0"), consensus_at=12, event_at=10) is None
