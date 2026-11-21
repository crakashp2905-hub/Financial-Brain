from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.data_permissions import (
    DataPermission, DataUse, PermissionStatus,
)
from financial_brain.providers.amfi import (
    bounded_history_windows, fetch_authorized_history_windows,
)
from financial_brain.providers.base import FetchError, FetchResult


def _permissions(*uses):
    return tuple(DataPermission(
        source_id="AMFI", use=use, status=PermissionStatus.USE_ALLOWED,
        effective_from=date(2026, 1, 1), effective_through=None,
        evidence_ref="approved-amfi-history-terms", reviewer="data-owner",
    ) for use in uses)


class _Adapter:
    source_id = "AMFI"

    def __init__(self, fetch):
        self._fetch = fetch

    def fetch_window(self, start, end):
        return self._fetch(start, end)


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


def test_authorized_fetcher_never_requests_more_than_ninety_days():
    requested = []
    permissions = _permissions(DataUse.RESEARCH, DataUse.RETENTION)
    result = FetchResult(b"payload", "https://authorized.invalid", datetime.now(timezone.utc),
                         "history.txt")

    fetched = fetch_authorized_history_windows(
        date(2026, 1, 1), date(2026, 6, 30),
        adapter=_Adapter(lambda start, end: (requested.append((start, end)) or result)),
        source_id="AMFI", permission_checked_on=date(2026, 11, 22),
        permissions=permissions, min_interval_seconds=0,
    )

    assert requested == bounded_history_windows(date(2026, 1, 1), date(2026, 6, 30))
    assert all((end - start).days + 1 <= 90 for start, end in requested)
    assert len(fetched) == 3
    assert [item[2] for item in fetched] == [result, result, result]


def test_authorized_fetcher_requires_both_current_data_permissions_before_call():
    calls = []
    with pytest.raises(PermissionError, match="RETENTION"):
        fetch_authorized_history_windows(
            date(2026, 1, 1), date(2026, 1, 2),
            adapter=_Adapter(lambda *_: calls.append(True)),
            source_id="AMFI", permission_checked_on=date(2026, 11, 22),
            permissions=_permissions(DataUse.RESEARCH),
        )
    assert calls == []


def test_authorized_fetcher_retries_transient_errors_and_spaces_requests():
    class FakeClock:
        now = 0.0

        def sleep(self, seconds):
            self.now += seconds

        def monotonic(self):
            return self.now

    clock = FakeClock()
    calls = []
    result = FetchResult(b"payload", "https://authorized.invalid", datetime.now(timezone.utc),
                         "history.txt")

    def fetch(start, end):
        calls.append((start, end, clock.now))
        if len(calls) == 1:
            raise FetchError("temporary", retryable=True)
        return result

    fetched = fetch_authorized_history_windows(
        date(2026, 1, 1), date(2026, 1, 1) + timedelta(days=90),
        adapter=_Adapter(fetch), source_id="AMFI",
        permission_checked_on=date(2026, 11, 22),
        permissions=_permissions(DataUse.RESEARCH, DataUse.RETENTION),
        min_interval_seconds=2, retry_backoff_seconds=1,
        sleep=clock.sleep, monotonic=clock.monotonic,
    )

    assert len(calls) == 3  # transient retry plus the second bounded window
    assert calls[1][2] - calls[0][2] >= 2
    assert calls[2][2] - calls[1][2] >= 2
    assert len(fetched) == 2


def test_authorized_fetcher_does_not_retry_terminal_source_errors():
    calls = []

    def forbidden(*_):
        calls.append(True)
        raise FetchError("forbidden", retryable=False)

    with pytest.raises(FetchError, match="forbidden"):
        fetch_authorized_history_windows(
            date(2026, 1, 1), date(2026, 1, 2),
            adapter=_Adapter(forbidden),
            source_id="AMFI", permission_checked_on=date(2026, 11, 22),
            permissions=_permissions(DataUse.RESEARCH, DataUse.RETENTION),
            max_attempts=3, min_interval_seconds=0,
        )
    assert calls == [True]
