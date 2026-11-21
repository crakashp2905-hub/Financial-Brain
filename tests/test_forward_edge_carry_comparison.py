from datetime import date, timedelta

import pytest

from financial_brain.forward_edge.carry_comparison import (
    CarryAlternative,
    CarryComparisonError,
    FuturesCarryPath,
    compare_futures_carry,
)


def _path(identity="future", day=date(2026, 11, 16), instrument="FUT-A", direction="LONG",
          spot=1000, future=990, exit_future=1005, costs=(10, 20, 30)):
    return FuturesCarryPath(identity, day, instrument, "IT", "L1", direction, 5, 10,
                            spot, future, spot + 2, exit_future, 9900, *costs)


def _alt(identity="cash", day=date(2026, 11, 16), instrument="CASH-B", value=0.004,
         direction="LONG", duration=5, notional=10000, sector="IT", liquidity="L1"):
    return CarryAlternative(identity, day, instrument, sector, liquidity, direction, duration,
                            notional, value)


def test_negative_basis_and_futures_carry_are_compared_after_all_costs() -> None:
    path = _path()
    assert path.entry_basis == -10
    assert path.annualized_entry_basis < 0
    assert path.gross_price_pnl == 150
    assert path.net_return == pytest.approx(90 / 9900)
    report = compare_futures_carry((path,), (_alt(),))
    assert report.pairs[0].alternative_net_return == 0.004
    assert report.pairs[0].incremental_net_return == pytest.approx(90 / 9900 - 0.004)
    assert not report.unmatched_futures_ids


def test_unmatched_or_different_session_alternatives_are_retained() -> None:
    path = _path()
    controls = (_alt("other-day", day=path.session + timedelta(days=1)),
                _alt("other-side", direction="SHORT"))
    report = compare_futures_carry((path,), controls)
    assert report.pairs == ()
    assert report.unmatched_futures_ids == ("future",)
    assert len(report.unused_alternative_ids) == 2


def test_invalid_cost_and_duplicate_observation_rejected() -> None:
    with pytest.raises(CarryComparisonError, match="non-negative"):
        _path(costs=(1, -1, 0))
    path = _path()
    alt = _alt("future")
    with pytest.raises(CarryComparisonError, match="unique"):
        compare_futures_carry((path,), (alt,))
