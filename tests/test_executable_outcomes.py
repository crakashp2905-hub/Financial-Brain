from datetime import date

import pytest

from financial_brain.evaluation import champion
from financial_brain.evaluation.executable import (
    ExecutableOutcome,
    ExecutableResolutionError,
    outcome_sha256,
)


DAY = date(2026, 11, 2)


def outcome(*, fill: float, gross: float | None, cost: float) -> ExecutableOutcome:
    row = ExecutableOutcome(DAY, "momentum", "ABC", fill, gross, cost, "0" * 64)
    return ExecutableOutcome(DAY, "momentum", "ABC", fill, gross, cost, outcome_sha256(row))


def resolve(row: ExecutableOutcome | None, *, annual_cash_rate: float = 0.0):
    return champion.resolve_executable(
        selected=[(DAY, "momentum", "ABC")], outcomes=[] if row is None else [row],
        scheduled_days=[DAY], horizon_sessions=1, annual_cash_rate=annual_cash_rate)


def test_no_fill_earns_only_cash_rate_and_never_fabricates_a_trade():
    cash_row = resolve(outcome(fill=0.0, gross=None, cost=0.0), annual_cash_rate=0.05)
    assert cash_row["sessions"][DAY.isoformat()]["filled_fraction"] == 0.0
    assert cash_row["sessions"][DAY.isoformat()]["net_return"] == pytest.approx((1.05 ** (1 / 252)) - 1)
    assert cash_row["promotion_eligible"] is False


def test_partial_fill_nets_itemized_cost_and_cash_on_unfilled_fraction():
    result = resolve(outcome(fill=0.5, gross=0.10, cost=0.02), annual_cash_rate=0.0)
    row = result["sessions"][DAY.isoformat()]
    assert row["net_return"] == pytest.approx(0.5 * (0.10 - 0.02))
    assert row["cost_fraction"] == pytest.approx(0.01)
    assert row["filled_fraction"] == pytest.approx(0.5)


def test_missing_outcome_receives_worst_case_penalty():
    result = resolve(None)
    row = result["sessions"][DAY.isoformat()]
    assert row["net_return"] == -1.0
    assert row["missing_count"] == 1
    assert result["missing_outcome_count"] == 1


def test_unknown_return_penalizes_only_filled_slice():
    result = resolve(outcome(fill=0.25, gross=None, cost=0.01))
    assert result["sessions"][DAY.isoformat()]["net_return"] == pytest.approx(-0.25 * 1.01)


def test_outcome_hash_and_schedule_are_mandatory():
    row = outcome(fill=1.0, gross=0.01, cost=0.001)
    bad = ExecutableOutcome(DAY, "momentum", "ABC", 1.0, 0.01, 0.001, "f" * 64)
    with pytest.raises(ExecutableResolutionError, match="hash"):
        resolve(bad)
    with pytest.raises(ExecutableResolutionError, match="scheduled_days"):
        champion.resolve_executable(selected=[(DAY, "momentum", "ABC")], outcomes=[row],
                                    scheduled_days=[DAY, DAY], horizon_sessions=1, annual_cash_rate=0.0)
