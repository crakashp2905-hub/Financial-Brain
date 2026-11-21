from __future__ import annotations

from datetime import datetime, timedelta, timezone

from financial_brain.kernel.data_quality import Bar, Revision, Revisions, clean_series, is_fresh, validate_bar

T0 = datetime(2026, 11, 9, 3, 45, tzinfo=timezone.utc)


def _bar(minute: int, o: float = 10, h: float = 11, l: float = 9, c: float = 10.5, v: float = 100) -> Bar:
    return Bar(T0 + timedelta(minutes=minute), o, h, l, c, v)


def test_valid_bar_has_no_reasons() -> None:
    assert validate_bar(_bar(0)) == []


def test_invalid_bars_get_reason_codes() -> None:
    assert "LOW_ABOVE_HIGH" in validate_bar(_bar(0, h=8, l=9))
    assert "NEGATIVE_VOLUME" in validate_bar(_bar(0, v=-1))
    assert "CLOSE_OUTSIDE_RANGE" in validate_bar(_bar(0, c=12))
    assert validate_bar(_bar(0, o=float("nan"))) == ["NON_FINITE"]


def test_identical_duplicates_collapse_and_conflicts_are_quarantined() -> None:
    dup = _bar(1)
    conflict = _bar(1, c=10.9)
    result = clean_series([_bar(0), dup, dup, conflict, _bar(2)])
    assert [b.ts for b in result.clean] == [T0, T0 + timedelta(minutes=2)]
    codes = [reason for _, rs in result.quarantine for reason in rs]
    # every row at a conflicted key is quarantined (both copies and the conflicting row): the key is ambiguous
    assert codes.count("CONFLICTING_DUPLICATE") == 3


def test_bad_rows_never_enter_clean_series() -> None:
    result = clean_series([_bar(0), _bar(1, v=-5)])
    assert len(result.clean) == 1
    assert result.quarantine[0][1] == ["NEGATIVE_VOLUME"]


def test_output_is_sorted_regardless_of_input_order() -> None:
    a = clean_series([_bar(3), _bar(1), _bar(2)])
    b = clean_series([_bar(2), _bar(3), _bar(1)])
    assert a.clean == b.clean


def test_as_of_never_sees_later_published_revision() -> None:
    store = Revisions()
    store.add(Revision("ABC:eps", 5.0, T0, T0))
    store.add(Revision("ABC:eps", 5.4, T0 + timedelta(days=10), T0 + timedelta(days=9)))
    assert store.as_of("ABC:eps", T0 + timedelta(days=1)).value == 5.0
    assert store.as_of("ABC:eps", T0 + timedelta(days=11)).value == 5.4
    assert store.as_of("ABC:eps", T0 - timedelta(days=1)) is None


def test_observation_cannot_be_published_before_observed() -> None:
    store = Revisions()
    try:
        store.add(Revision("k", 1, T0, T0 + timedelta(hours=1)))
    except ValueError:
        return
    raise AssertionError("expected refusal")


def test_stale_data_blocks() -> None:
    assert is_fresh(T0, T0 + timedelta(minutes=5), timedelta(minutes=10))
    assert not is_fresh(T0, T0 + timedelta(hours=2), timedelta(minutes=10))
