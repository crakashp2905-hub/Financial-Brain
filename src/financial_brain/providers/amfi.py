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

from collections import Counter
from datetime import date, datetime, timedelta

from .base import FetchError, FetchResult, Provider

NAV_ALL = "https://portal.amfiindia.com/spages/NAVAll.txt"
HEADER = "Scheme Code"
MAX_HISTORY_WINDOW_DAYS = 90


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


def parse(payload: bytes) -> list[dict]:
    """Rows of the NAV file, with the fund house and scheme type each row sits under.

    Rows whose NAV is not a number ("N.A." for a scheme that did not declare one) are
    dropped rather than stored as null: a missing NAV is an absence, not a price.
    """
    fund_house = scheme_type = None
    out: list[dict] = []
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
        try:
            on = datetime.strptime(nav_date, "%d-%b-%Y").date()
        except ValueError:
            continue
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
) -> dict[str, object]:
    """Report mixed-date, repeated, stale, and unavailable rows without inventing NAVs.

    Staleness is relative to the latest valid date in this file and uses an explicit
    caller-supplied calendar-day threshold. Missing schemes are identified only when
    a known, point-in-time scheme-code universe is supplied.
    """
    if stale_after_days < 0:
        raise ValueError("stale_after_days cannot be negative")
    decoded = _decode(payload)
    rows = parse(payload)
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
