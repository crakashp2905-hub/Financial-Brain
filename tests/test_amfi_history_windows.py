from datetime import date, timedelta

import pytest

from financial_brain.providers.amfi import bounded_history_windows


def test_history_windows_are_inclusive_contiguous_and_at_most_90_days():
    start, end = date(2026, 1, 1), date(2026, 7, 31)

    windows = bounded_history_windows(start, end)

    assert windows[0][0] == start
    assert windows[-1][1] == end
    assert all((right - left).days + 1 <= 90 for left, right in windows)
    assert all(windows[i][1] + timedelta(days=1) == windows[i + 1][0]
               for i in range(len(windows) - 1))


def test_exactly_90_days_is_one_window_and_91_days_is_two():
    start = date(2026, 1, 1)

    assert bounded_history_windows(start, start + timedelta(days=89)) == [
        (start, start + timedelta(days=89))
    ]
    assert bounded_history_windows(start, start + timedelta(days=90)) == [
        (start, start + timedelta(days=89)),
        (start + timedelta(days=90), start + timedelta(days=90)),
    ]


@pytest.mark.parametrize("kwargs", [{"max_days": 0}, {"max_days": 91}])
def test_history_windows_cannot_exceed_provider_limit(kwargs):
    with pytest.raises(ValueError, match="max_days"):
        bounded_history_windows(date(2026, 1, 1), date(2026, 2, 1), **kwargs)


def test_history_windows_reject_reversed_range():
    with pytest.raises(ValueError, match="on or before"):
        bounded_history_windows(date(2026, 2, 1), date(2026, 1, 1))
