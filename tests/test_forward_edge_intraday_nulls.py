from datetime import date, timedelta

import pytest

from financial_brain.forward_edge.intraday_nulls import (
    IntradayNullError,
    IntradayReturn,
    compare_intraday_time_side_null,
)


def _row(identity, *, day, instrument, minute=45, side="BUY", value=0.0, role="NULL"):
    return IntradayReturn(identity, day, instrument, minute, side, value, role)


def test_intraday_signal_compares_after_cost_with_same_time_side_null() -> None:
    day = date(2026, 11, 16)
    rows = (_row("signal", day=day, instrument="S", value=0.008, role="SIGNAL"),
            _row("n1", day=day, instrument="A", value=0.001),
            _row("n2", day=day, instrument="B", value=0.003),
            _row("n3", day=day, instrument="C", value=-0.001),
            _row("wrong-time", day=day, instrument="D", minute=60, value=0.9),
            _row("wrong-side", day=day, instrument="E", side="SELL", value=0.9))
    report = compare_intraday_time_side_null(rows)
    assert len(report.pairs) == 1
    assert report.pairs[0].matched_null_count == 3
    assert report.pairs[0].mean_null_net_return == pytest.approx(0.001)
    assert report.pairs[0].incremental_net_return == pytest.approx(0.007)


def test_sparse_null_bins_stay_unmatched_instead_of_being_dropped() -> None:
    day = date(2026, 11, 16)
    rows = (_row("signal", day=day, instrument="S", value=0.02, role="SIGNAL"),
            _row("n1", day=day, instrument="A"), _row("n2", day=day, instrument="B"))
    report = compare_intraday_time_side_null(rows, minimum_null_count=3)
    assert report.pairs == ()
    assert report.unmatched_signal_ids == ("signal",)
    assert report.mean_incremental_net_return is None


def test_previous_session_or_duplicate_observations_are_not_accepted_as_peers() -> None:
    day = date(2026, 11, 16)
    rows = (_row("signal", day=day, instrument="S", value=0.02, role="SIGNAL"),
            *(_row(f"n{i}", day=day - timedelta(days=1), instrument=f"A{i}") for i in range(4)))
    report = compare_intraday_time_side_null(rows)
    assert report.unmatched_signal_ids == ("signal",)
    with pytest.raises(IntradayNullError, match="unique"):
        compare_intraday_time_side_null((rows[0], rows[1], rows[1]))
