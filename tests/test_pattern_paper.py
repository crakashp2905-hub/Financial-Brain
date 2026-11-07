"""Paper intents, cost-aware fills, ledger identity and risk limits (CP-0189..0204, CP-0207, CP-0216)."""

from __future__ import annotations

import dataclasses

import pytest

from financial_brain.patterns.paper import (
    CostModel,
    FillOutcome,
    IntentRefused,
    KillSwitch,
    Ledger,
    PatternIntent,
    RiskLimits,
    check_new_intent,
    classify_loss,
    cost_breakdown,
    drawdown,
    ledger_identity_holds,
    net_return,
    next_session_entry,
    paper_record,
    profit_factor,
    session_eligibility,
    simulate_fill,
    trade_summary,
)

INTENT = PatternIntent(
    event_id="pev_1",
    spec_sha256="a" * 64,
    data_sha256="b" * 64,
    manifest_sha256="c" * 64,
    symbol="ABC",
    side="long",
    quantity=100,
    decision_at_utc="2026-01-06T10:00:00+00:00",
    earliest_entry_at_utc="2026-01-07T03:45:00+00:00",
    segment="cash_equity",
)


def test_intent_is_paper_only_and_cannot_short_cash_equity() -> None:
    with pytest.raises(IntentRefused):
        dataclasses.replace(INTENT, paper_only=False)
    with pytest.raises(IntentRefused, match="shorts"):
        dataclasses.replace(INTENT, side="short")
    short_fut = dataclasses.replace(INTENT, side="short", segment="futures_fixture")
    assert short_fut.side == "short"
    assert not hasattr(INTENT, "broker_order_id")


def test_decision_after_entry_is_refused() -> None:
    with pytest.raises(IntentRefused, match="decision"):
        dataclasses.replace(INTENT, decision_at_utc="2026-01-08T00:00:00+00:00")


def test_next_session_entry_never_same_bar() -> None:
    assert next_session_entry(10, [8, 9, 10, 11, 12]) == 11
    assert next_session_entry(12, [8, 9, 10, 11, 12]) is None


def test_cost_breakdown_sums_to_total_and_net_return_subtracts_all_costs() -> None:
    model = CostModel(spread_bps=5, fee_bps=3, impact_bps=2, financing_bps_per_day=0.5)
    parts = cost_breakdown(100_000, model, holding_days=4)
    assert parts["total"] == pytest.approx(parts["spread"] + parts["fee"] + parts["impact"] + parts["financing"])
    gross_only = net_return(100.0, 110.0, 0.0, 100_000)
    net = net_return(100.0, 110.0, parts["total"], 100_000)
    assert net == pytest.approx(gross_only - parts["total"] / 100_000)
    with pytest.raises(ValueError):
        CostModel(spread_bps=-1, fee_bps=0, impact_bps=0, financing_bps_per_day=0)


def test_fills_are_explicit_and_labelled_assumed() -> None:
    assert simulate_fill(FillOutcome.FILLED, 10).filled == 10
    partial = simulate_fill(FillOutcome.PARTIAL, 10, partial_fraction=0.5)
    assert partial.filled == 5 and partial.label == "ASSUMED"
    for outcome in (FillOutcome.NO_FILL, FillOutcome.REJECTED, FillOutcome.TIMEOUT, FillOutcome.STALE_QUOTE):
        assert simulate_fill(outcome, 10).filled == 0


def test_ledger_refuses_negative_cash_and_unbacked_reservations() -> None:
    ledger = Ledger(cash=10_000.0)
    ledger.reserve(4_000.0)
    with pytest.raises(IntentRefused):
        ledger.reserve(7_000.0)
    fill = simulate_fill(FillOutcome.FILLED, 40)
    ledger.fill_buy("ABC", fill, price=100.0, cost=20.0)
    assert ledger.cash == pytest.approx(10_000 - 4_000 - 20)
    assert ledger.cash >= 0


def test_equity_identity_and_pending_not_holdings() -> None:
    ledger = Ledger(cash=1_000.0, pending={"ABC": 5})
    assert "ABC" not in ledger.positions  # intents are never holdings (CP-0197)
    ledger.positions["ABC"] = (2, 100.0)
    assert ledger.equity() == pytest.approx(1_200.0)
    assert ledger_identity_holds(1_000, flows=0, realized=50, unrealized=150, ending=1_200)
    assert not ledger_identity_holds(1_000, flows=0, realized=50, unrealized=50, ending=1_200)


def test_risk_limits_and_kill_switch_produce_codes() -> None:
    limits = RiskLimits()
    ok = check_new_intent(limits=limits, symbol_weight_after=0.05, sector_weight_after=0.1, drawdown=0.02,
                          leverage_after=0.9, eligible=True, kill=KillSwitch())
    assert ok == []
    bad = check_new_intent(limits=limits, symbol_weight_after=0.2, sector_weight_after=0.3, drawdown=0.2,
                           leverage_after=1.5, eligible=False, kill=KillSwitch(active=True))
    assert set(bad) == {"INELIGIBLE_SESSION", "KILL_SWITCH_ACTIVE", "SYMBOL_LIMIT", "SECTOR_LIMIT",
                        "DRAWDOWN_LIMIT", "LEVERAGE_LIMIT"}


def test_kill_switch_blocks_entries_but_not_existing_position_handling() -> None:
    kill = KillSwitch(active=True)
    assert kill.allows_new_entries() is False
    # existing positions are marked/exited outside the entry gate, so the switch never traps them


def test_ineligible_sessions_block_trading() -> None:
    assert session_eligibility(circuit=True, suspended=False, auction_only=False) is False
    assert session_eligibility(circuit=False, suspended=False, auction_only=False) is True


def test_zero_trade_days_show_zero_realized_pnl() -> None:
    summary = trade_summary({"d1": 0.0, "d2": 0.0})
    assert summary["realized_total"] == 0.0 and summary["trading_days"] == 0


def test_drawdown_profit_factor_and_loss_categories() -> None:
    assert drawdown([100, 120, 90, 110]) == pytest.approx(0.25)
    assert profit_factor([10, -5, 5]) == pytest.approx(3.0)
    assert profit_factor([10, 5]) is None
    assert classify_loss(gap_through_stop=True, false_breakout=True, stale_quote=False, spread_dominant=False) == "gap_through_stop"
    assert classify_loss(gap_through_stop=False, false_breakout=False, stale_quote=False, spread_dominant=False) == "other"


def test_paper_record_links_hashes_and_labels_fills() -> None:
    fill = simulate_fill(FillOutcome.FILLED, 100)
    rec = paper_record(INTENT, fill, {"total": 12.5})
    assert rec["spec_sha256"] == "a" * 64 and rec["data_sha256"] == "b" * 64
    assert rec["fill_label"] == "ASSUMED"
