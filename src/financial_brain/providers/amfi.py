"""AMFI - the official daily NAV feed for every Indian mutual fund scheme (C02).

AMFI publishes one plain-text file with the current NAV of all ~18k schemes, ISINs
included, free and without authentication:

    Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;
    Option;Net Asset Value;Date

The file interleaves section headers with data rows - a scheme-type line
("Open Ended Schemes(Equity Scheme - Large Cap Fund)"), then a fund-house line
("Axis Mutual Fund"), then that house's schemes - so the parser carries both down the
file. Each row states its own date; the file is "as published", not "as of a date we
asked for", so rows are stored under the date they carry, never the date we fetched.

Tier 1: AMFI is the industry body that publishes these NAVs, not a scraper of them.
"""
from __future__ import annotations

import time
import re
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, timedelta
from math import isfinite
from typing import Any, Protocol, TypedDict

from ..forward_edge.data_permissions import DataPermission, DataUse
from .base import FetchError, FetchResult, Provider

NAV_ALL = "https://portal.amfiindia.com/spages/NAVAll.txt"
HEADER = "Scheme Code"
MAX_HISTORY_WINDOW_DAYS = 90


class AmfiQualityReport(TypedDict):
    rows: list[dict[str, Any]]
    latest_nav_date: str | None
    repeated_scheme_codes: list[str]
    stale_scheme_codes: list[str]
    unavailable: list[dict[str, str]]
    stale_after_days: int


class HistoryWindowAdapter(Protocol):
    """Adapter whose declared source identity is checked against data rights."""

    source_id: str

    def fetch_window(self, start_date: date, end_date: date) -> FetchResult: ...


def bounded_history_windows(
    start_date: date,
    end_date: date,
    *,
    max_days: int = MAX_HISTORY_WINDOW_DAYS,
) -> list[tuple[date, date]]:
    """Partition an inclusive history range into windows no longer than 90 days.

    This pure helper only constructs request bounds. It does not fetch data or grant
    permission to use an AMFI endpoint; callers must separately verify the provider's
    current terms and approved access method.
    """
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    if not 1 <= max_days <= MAX_HISTORY_WINDOW_DAYS:
        raise ValueError(f"max_days must be between 1 and {MAX_HISTORY_WINDOW_DAYS}")

    windows: list[tuple[date, date]] = []
    window_start = start_date
    while window_start <= end_date:
        window_end = min(end_date, window_start + timedelta(days=max_days - 1))
        windows.append((window_start, window_end))
        window_start = window_end + timedelta(days=1)
    return windows


def fetch_authorized_history_windows(
    start_date: date,
    end_date: date,
    *,
    adapter: HistoryWindowAdapter,
    source_id: str,
    permission_checked_on: date,
    permissions: tuple[DataPermission, ...],
    max_attempts: int = 3,
    min_interval_seconds: float = 2.0,
    retry_backoff_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> list[tuple[date, date, FetchResult]]:
    """Fetch through a permission-gated adapter using windows no longer than 90 days.

    This function does not choose an endpoint or grant source rights. The injected
    adapter must implement the separately approved access method. Effective RESEARCH
    and RETENTION permissions are checked before calling it; only retryable source
    failures are retried, and every request is separated by the configured interval.
    """
    if not source_id.strip():
        raise ValueError("source_id is required")
    if adapter.source_id != source_id:
        raise ValueError("history adapter source_id does not match permission source_id")
    if (isinstance(max_attempts, bool) or not isinstance(max_attempts, int)
            or max_attempts < 1):
        raise ValueError("max_attempts must be a positive integer")
    if (not isfinite(min_interval_seconds) or not isfinite(retry_backoff_seconds)
            or min_interval_seconds < 0 or retry_backoff_seconds < 0):
        raise ValueError("rate interval and retry backoff cannot be negative")
    for use in (DataUse.RESEARCH, DataUse.RETENTION):
        if not any(permission.allows(
            source_id=source_id, use=use, on=permission_checked_on
        ) for permission in permissions):
            raise PermissionError(
                f"source {source_id} lacks effective {use.value} permission"
            )

    windows = bounded_history_windows(start_date, end_date)
    fetched: list[tuple[date, date, FetchResult]] = []
    last_request_at: float | None = None
    for window_start, window_end in windows:
        for attempt in range(max_attempts):
            if last_request_at is not None:
                wait_seconds = min_interval_seconds - (monotonic() - last_request_at)
                if wait_seconds > 0:
                    sleep(wait_seconds)
            last_request_at = monotonic()
            try:
                result = adapter.fetch_window(window_start, window_end)
                if not isinstance(result, FetchResult):
                    raise TypeError("history adapter must return FetchResult")
                fetched.append((window_start, window_end, result))
                break
            except FetchError as exc:
                if not exc.retryable or attempt + 1 == max_attempts:
                    raise
                sleep(retry_backoff_seconds * (2 ** attempt))
    return fetched


class AmfiNav(Provider):
    source = "AMFI"
    dataset = "mf_nav"
    tier = 1

    def fetch(self, business_date: date) -> FetchResult:
        payload, status, ctype = self._http_get(NAV_ALL, timeout=120)
        if HEADER.encode() not in payload[:200]:
            raise FetchError(f"NAVAll.txt did not start with the expected header: "
                             f"{payload[:80]!r}", retryable=True, status=status)
        return FetchResult(payload=payload, url=NAV_ALL, retrieved_at=self._now(),
                           filename=f"NAVAll-{business_date.isoformat()}.txt",
                           content_type=ctype, http_status=status)


def _decode(payload: bytes) -> str:
    """AMFI publishes UTF-8 today. Try it strictly, then fall back to Windows-1252, which
    older exports of this file used - so a change of encoding degrades rather than
    raising mid-ingest."""
    for encoding in ("utf-8", "cp1252"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def parse(payload: bytes, *, reject_duplicate_scheme_dates: bool = True) -> list[dict]:
    """Rows of the NAV file, with the fund house and scheme type each row sits under.

    Rows whose NAV is not a number ("N.A." for a scheme that did not declare one) are
    dropped rather than stored as null: a missing NAV is an absence, not a price.
    """
    fund_house = scheme_type = None
    out: list[dict] = []
    seen_scheme_dates: set[tuple[str, date]] = set()
    for raw in _decode(payload).splitlines():
        line = raw.strip()
        if not line or line.startswith(HEADER):
            continue
        if ";" not in line:
            # A section header: scheme types name themselves, everything else is a house.
            if "Scheme" in line and "(" in line:
                scheme_type = line
            else:
                fund_house = line
            continue
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 8:
            continue
        code, isin_g, isin_r, name, plan, option, nav, nav_date = parts[:8]
        try:
            value = float(nav)
        except ValueError:
            continue
        if not isfinite(value):
            continue
        try:
            on = datetime.strptime(nav_date, "%d-%b-%Y").date()
        except ValueError:
            continue
        identity = (code, on)
        if identity in seen_scheme_dates and reject_duplicate_scheme_dates:
            raise FetchError(
                f"duplicate AMFI scheme/date identity: {code} on {on.isoformat()}",
                retryable=False,
            )
        seen_scheme_dates.add(identity)
        out.append({"scheme_code": code, "isin_growth": isin_g if isin_g != "-" else None,
                    "isin_reinvest": isin_r if isin_r != "-" else None,
                    "scheme_name": name, "fund_house": fund_house,
                    "scheme_type": scheme_type, "plan": plan or None,
                    "option": option or None, "nav": value, "nav_date": on})
    return out


def parse_quality_report(
    payload: bytes,
    *,
    stale_after_days: int,
    known_scheme_codes: tuple[str, ...] = (),
) -> AmfiQualityReport:
    """Report mixed-date, repeated, stale, and unavailable rows without inventing NAVs.

    Staleness is relative to the latest valid date in this file and uses an explicit
    caller-supplied calendar-day threshold. Missing schemes are identified only when
    a known, point-in-time scheme-code universe is supplied.
    """
    if stale_after_days < 0:
        raise ValueError("stale_after_days cannot be negative")
    decoded = _decode(payload)
    # Quality reports must surface repeated identities instead of failing the entire
    # report; strict ingestion uses parse()'s default fail-closed behavior.
    rows = parse(payload, reject_duplicate_scheme_dates=False)
    dated: list[tuple[str, date]] = [(row["scheme_code"], row["nav_date"]) for row in rows]
    reported: list[tuple[str, date]] = list(dated)
    unavailable: list[dict[str, str]] = []
    for raw in decoded.splitlines():
        line = raw.strip()
        if not line or ";" not in line or line.startswith(HEADER):
            continue
        parts = [part.strip() for part in line.split(";")]
        if len(parts) < 8 or parts[6].upper() not in {"N.A.", "NA", ""}:
            continue
        try:
            nav_date = datetime.strptime(parts[7], "%d-%b-%Y").date()
        except ValueError:
            continue
        unavailable.append({"scheme_code": parts[0], "scheme_name": parts[3],
                            "nav_date": nav_date.isoformat(), "reason": "NAV_UNAVAILABLE"})
        reported.append((parts[0], nav_date))

    latest = max((on for _, on in dated), default=None)
    counts = Counter(code for code, _ in reported)
    stale = sorted({code for code, on in dated
                    if latest is not None and (latest - on).days > stale_after_days})
    present = {code for code, _ in reported}
    unavailable.extend({"scheme_code": code, "scheme_name": "", "nav_date": "",
                        "reason": "MISSING_FROM_SNAPSHOT"}
                       for code in sorted(set(known_scheme_codes) - present))
    return {
        "rows": rows,
        "latest_nav_date": latest.isoformat() if latest is not None else None,
        "repeated_scheme_codes": sorted(code for code, count in counts.items() if count > 1),
        "stale_scheme_codes": stale,
        "unavailable": unavailable,
        "stale_after_days": stale_after_days,
    }


def classify_scheme(row: dict[str, Any]) -> dict[str, str]:
    """Normalize only classifications explicitly supported by AMFI descriptors."""
    plan_text = " ".join(str(row.get(key) or "") for key in ("plan", "scheme_name")).upper()
    plan_type = ("DIRECT" if re.search(r"\bDIRECT\b", plan_text) else
                 "REGULAR" if re.search(r"\bREGULAR\b", plan_text) else "UNCLASSIFIED")

    option_text = " ".join(str(row.get(key) or "") for key in ("option", "scheme_name")).upper()
    if "REINVEST" in option_text:
        payout_type = "IDCW_REINVESTMENT"
    elif re.search(r"\b(PAYOUT|DIVIDEND)\b|\bIDCW\b", option_text):
        payout_type = ("IDCW_PAYOUT" if "PAYOUT" in option_text or "DIVIDEND" in option_text
                       else "IDCW_UNSPECIFIED")
    elif re.search(r"\bGROWTH\b", option_text):
        payout_type = "GROWTH"
    else:
        payout_type = "UNCLASSIFIED"

    descriptor = " ".join(str(row.get(key) or "")
                          for key in ("scheme_type", "scheme_name")).upper()
    structure_type = ("INTERVAL" if "INTERVAL" in descriptor else
                      "CLOSED_ENDED" if "CLOSE ENDED" in descriptor else
                      "OPEN_ENDED" if "OPEN ENDED" in descriptor else "UNCLASSIFIED")
    if "FUND OF FUNDS" in descriptor or re.search(r"\bFOF\b", descriptor):
        asset_class = "FUND_OF_FUNDS"
    elif re.search(r"\b(EQUITY|LARGE CAP|MID CAP|SMALL CAP|ELSS)\b", descriptor):
        asset_class = "EQUITY"
    elif re.search(r"\b(DEBT|GILT|BOND|LIQUID|MONEY MARKET|FIXED INCOME)\b", descriptor):
        asset_class = "DEBT"
    elif "HYBRID" in descriptor or "BALANCED" in descriptor:
        asset_class = "HYBRID"
    elif re.search(r"\b(GOLD|SILVER|COMMODITY)\b", descriptor):
        asset_class = "COMMODITY"
    else:
        asset_class = "UNCLASSIFIED"
    management_type = ("PASSIVE" if re.search(
        r"\b(INDEX|PASSIVE|ETF|EXCHANGE TRADED)\b", descriptor) else
        "ACTIVE" if "ACTIVELY MANAGED" in descriptor else "UNCLASSIFIED")
    return {"plan_type": plan_type, "payout_type": payout_type,
            "structure_type": structure_type, "management_type": management_type,
            "asset_class": asset_class}

def nav_coverage(rows: list[dict], expected_scheme_codes: list[str], *, as_of: date,
                 stale_after_days: int = 7) -> list[dict]:
    """Classify expected schemes by their latest NAV date without relabelling old rows."""
    if stale_after_days < 0:
        raise ValueError("stale_after_days must be non-negative")
    latest: dict[str, date] = {}
    for row in rows:
        code, day = str(row.get("scheme_code", "")), row.get("nav_date")
        if code and isinstance(day, date) and day <= as_of:
            latest[code] = max(day, latest.get(code, day))
    out = []
    for code in sorted(set(str(value) for value in expected_scheme_codes)):
        day = latest.get(code)
        age = (as_of - day).days if day is not None else None
        out.append({"scheme_code": code, "latest_nav_date": day, "age_days": age,
                    "status": "UNAVAILABLE" if day is None else
                    "STALE" if age > stale_after_days else "CURRENT"})
    return out
