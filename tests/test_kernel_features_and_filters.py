from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.features_and_filters import (
    SideRules,
    ThesisJournal,
    entry_filter,
    imbalance,
    recommend,
    side_costs,
)


def test_journal_is_append_only() -> None:
    journal = ThesisJournal()
    journal.add("2026-11-09", "margin expansion thesis")
    journal.add("2026-11-10", "amended: margin thesis weakened")
    assert len(journal.history()) == 2
    assert journal.history()[0][1] == "margin expansion thesis"
    with pytest.raises(ValueError):
        journal.add("2026-11-11", "   ")


def test_recommend_states_never_invent_a_trade() -> None:
    assert recommend(None, history_days=300, min_history=252, threshold=0.1) == "INSUFFICIENT_DATA"
    assert recommend(0.05, history_days=300, min_history=252, threshold=0.1) == "NO_TRADE"
    assert recommend(0.5, history_days=300, min_history=252, threshold=0.1) == "LONG"
    assert recommend(-0.5, history_days=300, min_history=252, threshold=0.1) == "SHORT"


def test_order_flow_imbalance_bounds_and_empty_depth() -> None:
    assert imbalance([30, 10], [10, 10]) == pytest.approx(1 / 3)  # (40 - 20) / 60
    assert imbalance([], []) is None


def test_entry_filter_reports_spread_and_queue_reasons() -> None:
    assert entry_filter(Decimal("0.01"), Decimal("100"), Decimal("5"), queue_ahead=10, max_queue=50) == []
    reasons = entry_filter(Decimal("1"), Decimal("100"), Decimal("5"), queue_ahead=80, max_queue=50)
    assert "spread too wide" in reasons and "queue ahead too deep" in reasons


def test_short_costs_include_borrow_and_restricted_sides_are_refused() -> None:
    rules = SideRules(fee_bps=Decimal("3"), borrow_bps_per_day=Decimal("1"), restricted=False, borrow_available=True)
    assert side_costs("LONG", Decimal("100000"), 10, rules) == Decimal("30")
    assert side_costs("SHORT", Decimal("100000"), 10, rules) == Decimal("30") + Decimal("100")
    blocked = SideRules(Decimal("3"), Decimal("1"), restricted=True, borrow_available=True)
    with pytest.raises(ValueError, match="restricted"):
        side_costs("LONG", Decimal("1"), 1, blocked)
    no_borrow = SideRules(Decimal("3"), Decimal("1"), restricted=False, borrow_available=False)
    with pytest.raises(ValueError, match="borrow"):
        side_costs("SHORT", Decimal("1"), 1, no_borrow)
