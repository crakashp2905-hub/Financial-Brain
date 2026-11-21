from datetime import date, datetime, timezone

import pytest

from financial_brain.forward_edge.capital_allocation import (
    AllocatedSession,
    CapitalAllocationError,
    FrozenCapitalAllocation,
    evaluate_frozen_allocation,
)


def _allocation():
    return FrozenCapitalAllocation("alloc-v1", datetime(2026, 11, 15, tzinfo=timezone.utc),
                                   date(2026, 11, 16), 1_000_000,
                                   {"momentum": 0.4, "patterns": 0.3}, 0.3)


def test_fixed_allocation_keeps_losing_subbook_and_cash_sleeve_in_combined_return() -> None:
    allocation = _allocation()
    sessions = (AllocatedSession(date(2026, 11, 16), {"momentum": 0.02, "patterns": -0.05}, 0.001),
                AllocatedSession(date(2026, 11, 17), {"momentum": -0.01, "patterns": -0.02}, 0.001))
    report = evaluate_frozen_allocation(allocation, sessions)
    expected_day1 = 0.4 * 0.02 + 0.3 * -0.05 + 0.3 * 0.001
    assert report.sessions[0]["weighted_portfolio_return"] == pytest.approx(expected_day1)
    assert report.retained_book_ids == ("momentum", "patterns")
    assert report.sessions[0]["book_returns"]["patterns"] == -0.05
    assert report.sessions[1]["book_returns"]["patterns"] == -0.02
    assert not report.promotion_allowed


def test_missing_losing_book_cannot_be_silently_removed() -> None:
    with pytest.raises(CapitalAllocationError, match="every frozen book"):
        evaluate_frozen_allocation(_allocation(),
                                  (AllocatedSession(date(2026, 11, 16), {"momentum": 0.02}, 0),))


def test_allocation_weights_and_freeze_date_are_enforced() -> None:
    with pytest.raises(CapitalAllocationError, match="sum to one"):
        FrozenCapitalAllocation("bad", datetime(2026, 11, 15, tzinfo=timezone.utc),
                                date(2026, 11, 16), 1000, {"a": 0.8}, 0.3)
    with pytest.raises(CapitalAllocationError, match="before"):
        FrozenCapitalAllocation("late", datetime(2026, 11, 16, tzinfo=timezone.utc),
                                date(2026, 11, 16), 1000, {"a": 0.8}, 0.2)
