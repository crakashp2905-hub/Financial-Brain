from decimal import Decimal

import pytest

from financial_brain.kernel.exit_accounting import (
    ExitAccountingError, ExitFill, PositionExitLedger, TrailingStopState,
)


def test_long_trailing_stop_only_moves_towards_profit_with_same_policy():
    state = TrailingStopState.start(side="BUY", policy_version="p1", distance="5", entry_price="100")
    advanced = state.observe("110", policy_version="p1")
    retraced = advanced.observe("105", policy_version="p1")
    assert (state.stop_price, advanced.stop_price, retraced.stop_price) == (
        Decimal("95"), Decimal("105"), Decimal("105"))
    assert retraced.watermark == Decimal("110")


def test_short_trailing_stop_only_moves_towards_profit_with_same_policy():
    state = TrailingStopState.start(side="SELL_SHORT", policy_version="p1", distance="5", entry_price="100")
    advanced = state.observe("90", policy_version="p1")
    retraced = advanced.observe("95", policy_version="p1")
    assert (state.stop_price, advanced.stop_price, retraced.stop_price) == (
        Decimal("105"), Decimal("95"), Decimal("95"))
    assert retraced.watermark == Decimal("90")


def test_policy_version_change_is_explicit_new_trail():
    state = TrailingStopState.start(side="BUY", policy_version="p1", distance="5", entry_price="100")
    updated = state.observe("90", policy_version="p2")
    assert updated.policy_version == "p2"
    assert updated.watermark == Decimal("90")
    assert updated.stop_price == Decimal("85")


def test_staged_realized_pnl_and_open_quantity_reconcile_without_double_counting():
    ledger = PositionExitLedger("BUY", Decimal("100"), Decimal("10"))
    first = ExitFill("tp-1", Decimal("4"), Decimal("120"), Decimal("2"))
    ledger = ledger.apply(first)
    assert ledger.remaining_quantity == Decimal("6")
    assert ledger.realized_pnl == Decimal("78")
    assert ledger.marked_total_pnl("110") == Decimal("138")
    assert ledger.apply(first) is ledger  # replay does not double book
    ledger = ledger.apply(ExitFill("stop-rest", Decimal("6"), Decimal("95"), Decimal("3")))
    assert ledger.remaining_quantity == 0
    assert ledger.realized_pnl == Decimal("45")
    assert ledger.marked_total_pnl("100") == Decimal("45")


def test_short_position_and_quantity_overrun_are_accounted_and_rejected():
    ledger = PositionExitLedger("SELL_SHORT", Decimal("100"), Decimal("3"))
    ledger = ledger.apply(ExitFill("cover", Decimal("1"), Decimal("90")))
    assert ledger.realized_pnl == Decimal("10")
    assert ledger.marked_total_pnl("95") == Decimal("20")
    with pytest.raises(ExitAccountingError, match="cannot exceed"):
        ledger.apply(ExitFill("over-close", Decimal("3"), Decimal("90")))


def test_conflicting_fill_replay_and_duplicate_event_construction_fail_closed():
    row = ExitFill("event", Decimal("1"), Decimal("101"))
    ledger = PositionExitLedger("BUY", Decimal("100"), Decimal("2"), (row,))
    with pytest.raises(ExitAccountingError, match="conflicts"):
        ledger.apply(ExitFill("event", Decimal("1"), Decimal("102")))
    with pytest.raises(ExitAccountingError, match="duplicate"):
        PositionExitLedger("BUY", Decimal("100"), Decimal("2"), (row, row))
