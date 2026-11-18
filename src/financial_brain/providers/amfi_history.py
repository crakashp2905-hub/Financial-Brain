"""AMFI NAV history ingestion plan (FB-0305 code side, FB-0306).

No network code lives here: callers pass a ``fetch(start, end) -> str`` that hits the official
AMFI history export. This module
* splits a date range into windows of at most 90 days (AMFI's per-download limit) and refuses to
  widen it,
* resumes incrementally from the last stored date,
* retries a failed window with bounded exponential backoff and spaces calls by a minimum interval,
* parses the semicolon export, keeping only rows inside the requested window.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

AMFI_MAX_WINDOW_DAYS = 90


class AmfiHistoryError(ValueError):
    pass


def windows(start: date, end: date, max_days: int = AMFI_MAX_WINDOW_DAYS) -> list[tuple[date, date]]:
    """Inclusive windows covering start..end, each spanning at most ``max_days`` days."""
    if max_days > AMFI_MAX_WINDOW_DAYS:
        raise AmfiHistoryError("window wider than AMFI's 90-day limit")
    if max_days < 1 or end < start:
        raise AmfiHistoryError("bad range")
    out, s = [], start
    while s <= end:
        e = min(s + timedelta(days=max_days - 1), end)
        out.append((s, e))
        s = e + timedelta(days=1)
    return out


def resume_from(last_stored: date | None, earliest: date) -> date:
    return earliest if last_stored is None else max(earliest, last_stored + timedelta(days=1))


@dataclass(frozen=True)
class NavRow:
    scheme_code: str
    scheme_name: str
    nav: Decimal
    nav_date: date


def parse(text: str, lo: date, hi: date) -> list[NavRow]:
    rows = []
    for line in text.splitlines():
        p = [x.strip() for x in line.split(";")]
        if len(p) < 8 or not p[0].isdigit():
            continue
        try:
            nav = Decimal(p[4])
            d = datetime.strptime(p[7], "%d-%b-%Y").date()
        except (InvalidOperation, ValueError):
            continue
        if lo <= d <= hi:
            rows.append(NavRow(p[0], p[1], nav, d))
    return rows


def ingest(start: date, end: date, fetch: Callable[[date, date], str], *,
           sleep: Callable[[float], None], retries: int = 3, backoff: float = 2.0,
           min_interval: float = 1.0) -> tuple[list[NavRow], list[tuple[date, date]]]:
    """Fetch every window; returns (rows, windows that failed after all retries)."""
    rows: list[NavRow] = []
    failed = []
    for i, (lo, hi) in enumerate(windows(start, end)):
        if i:
            sleep(min_interval)
        for attempt in range(retries + 1):
            try:
                rows += parse(fetch(lo, hi), lo, hi)
                break
            except (OSError, TimeoutError):
                if attempt == retries:
                    failed.append((lo, hi))
                else:
                    sleep(backoff * 2 ** attempt)
    return rows, failed
