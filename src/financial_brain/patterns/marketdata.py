"""Market-data contracts for chart patterns (stage 03: CP-0043..0055).

Everything here is offline and read-only. Timestamps are normalised to Asia/Kolkata
(fixed +05:30, which has been the Indian standard time without daylight changes
since 1945) and persisted in UTC. Missing data is reported, never invented.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any

from financial_brain.patterns.offline import guard_external
from financial_brain.patterns.spec import canonical_json

IST = timezone(timedelta(hours=5, minutes=30), "Asia/Kolkata")
UTC = timezone.utc


class NaiveTimestamp(ValueError):
    """Timestamp lacks a timezone; refused rather than assumed to be IST."""


def normalize_timestamp(text: str) -> tuple[str, str]:
    """Return (IST display, UTC persisted) for an aware ISO-8601 timestamp (CP-0043)."""
    ts = datetime.fromisoformat(text)
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise NaiveTimestamp(f"naive timestamp refused: {text!r}")
    return ts.astimezone(IST).isoformat(), ts.astimezone(UTC).isoformat()


class SessionKind(str, Enum):
    NORMAL = "normal"
    SHORTENED = "shortened"
    HOLIDAY = "holiday"
    SPECIAL = "special"


@dataclass(frozen=True)
class SessionInfo:
    kind: SessionKind
    open_ist: str | None = None
    close_ist: str | None = None
    circuit_halt: bool = False


class SessionCalendar:
    """Versioned exchange-session table; weekday arithmetic is never used (CP-0044)."""

    def __init__(self, version: str, table: dict[date, SessionInfo]) -> None:
        self.version = version
        self._table = dict(table)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SessionCalendar:
        table: dict[date, SessionInfo] = {}
        for day, row in data["sessions"].items():
            table[date.fromisoformat(day)] = SessionInfo(
                SessionKind(row["kind"]), row.get("open"), row.get("close"), bool(row.get("circuit_halt", False))
            )
        return cls(str(data["version"]), table)

    def info(self, day: date) -> SessionInfo | None:
        return self._table.get(day)

    def is_trading(self, day: date) -> bool:
        info = self.info(day)
        return info is not None and info.kind is not SessionKind.HOLIDAY

    def trading_days(self, start: date, end: date) -> list[date]:
        return [d for d in sorted(self._table) if start <= d <= end and self.is_trading(d)]


FIXTURE_CALENDAR: dict[str, Any] = {
    "version": "fixture-2026.1",
    "sessions": {
        "2026-01-05": {"kind": "normal", "open": "09:15", "close": "15:30"},
        "2026-01-06": {"kind": "normal", "open": "09:15", "close": "15:30"},
        "2026-01-07": {"kind": "holiday"},
        "2026-01-08": {"kind": "shortened", "open": "09:15", "close": "13:00"},
        "2026-01-09": {"kind": "normal", "open": "09:15", "close": "15:30", "circuit_halt": True},
        "2026-01-12": {"kind": "special", "open": "18:00", "close": "19:00"},
    },
}


def fixture_calendar() -> SessionCalendar:
    return SessionCalendar.from_json(FIXTURE_CALENDAR)


@dataclass(frozen=True)
class SecurityIdentity:
    lineage: str
    isin: str


class IdentityMap:
    """Alias to canonical lineage. Aliases that point at two identities are withheld (CP-0046)."""

    def __init__(self) -> None:
        self._alias: dict[str, set[str]] = {}
        self._lineage: dict[str, SecurityIdentity] = {}

    def add(self, alias: str, identity: SecurityIdentity) -> None:
        self._lineage[identity.lineage] = identity
        self._alias.setdefault(alias, set()).add(identity.lineage)

    def resolve(self, alias: str) -> SecurityIdentity | None:
        lineages = self._alias.get(alias)
        if not lineages or len(lineages) != 1:
            return None  # unknown or ambiguous: withheld, never guessed
        return self._lineage[next(iter(lineages))]


def adjust_for_split(closes: list[float], ex_index: int, factor: float) -> list[float]:
    """Divide pre-split prices by ``factor`` so a split is not read as a crash (CP-0048)."""
    if factor <= 0 or not 0 < ex_index <= len(closes):
        raise ValueError("factor must be positive and ex_index inside the series")
    return [c / factor if i < ex_index else c for i, c in enumerate(closes)]


def snapshot(rows: list[tuple[str, float, float, float, float, float]]) -> dict[str, Any]:
    """Row count, session range and canonical SHA-256 of an OHLCV input (CP-0049)."""
    if not rows:
        return {"row_count": 0, "min_session": None, "max_session": None, "sha256": hashlib.sha256(b"[]").hexdigest()}
    ordered = sorted(rows, key=lambda r: r[0])
    digest = hashlib.sha256(canonical_json([list(r) for r in ordered])).hexdigest()
    return {
        "row_count": len(ordered),
        "min_session": ordered[0][0],
        "max_session": ordered[-1][0],
        "sha256": digest,
    }


def volume_eligibility(volumes: Iterable[float | None], *, max_zero_run: int = 3) -> str:
    """'OK' or 'DATA_INSUFFICIENT'; volume rules must not run on missing or suspicious volume (CP-0050)."""
    run = 0
    for v in volumes:
        if v is None or v != v or v < 0:
            return "DATA_INSUFFICIENT"
        run = run + 1 if v == 0 else 0
        if run > max_zero_run:
            return "DATA_INSUFFICIENT"
    return "OK"


class SegmentKind(str, Enum):
    CASH_EQUITY = "cash_equity"
    INDEX_FUTURE = "index_future"
    STOCK_FUTURE = "stock_future"


def shorting_allowed(segment: SegmentKind, *, has_fixture_support: bool) -> bool:
    """Shorts are restricted to explicitly supported futures fixtures (CP-0051)."""
    if segment is SegmentKind.CASH_EQUITY:
        return False
    return has_fixture_support


class DataTier(str, Enum):
    EOD_OHLC = "EOD_OHLC"
    INTRADAY_QUOTE = "INTRADAY_QUOTE"
    ORDER_BOOK = "ORDER_BOOK"


TIER_LABEL = {
    DataTier.EOD_OHLC: "end-of-day bars; fills are assumptions",
    DataTier.INTRADAY_QUOTE: "intraday quotes; fill observability limited",
    DataTier.ORDER_BOOK: "order-book depth available",
}


def missing_sessions(expected: list[date], present: set[date]) -> list[date]:
    """Sessions with no bar. They are listed, never forward-filled (CP-0053)."""
    return [d for d in expected if d not in present]


@dataclass(frozen=True)
class Freshness:
    status: str
    message: str


def freshness(*, last_session: date, as_of: date, expected_sessions_after: int, max_lag: int) -> Freshness:
    """Staleness check that tells the caller to abstain (CP-0054)."""
    if last_session > as_of:
        return Freshness("INVALID", "last session is after the as-of date")
    if expected_sessions_after > max_lag:
        return Freshness("STALE", f"{expected_sessions_after} sessions behind; abstain until refreshed")
    return Freshness("FRESH", "within the allowed lag")


ENTITLEMENTS: dict[str, dict[str, Any]] = {
    "local_eod_duckdb": {"available": True, "network": False},
    "intraday_quotes": {"available": False, "network": True},
    "order_book_depth": {"available": False, "network": True},
    "index_membership": {"available": False, "network": True},
}


def open_external_dataset(name: str) -> None:
    """Every optional external dataset is blocked in offline mode (CP-0055)."""
    entry = ENTITLEMENTS.get(name)
    if entry is None:
        raise KeyError(f"unknown dataset {name!r}")
    if entry["network"]:
        guard_external(f"open dataset {name}")
    if not entry["available"]:
        raise RuntimeError(f"dataset {name!r} is not installed in this checkout")


def to_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def adjust_for_dividend(closes: list[float], ex_index: int, amount: float) -> list[float]:
    """Scale pre-ex-date prices by the dividend factor so the ex-date gap is not a fake breakout (CP-0142)."""
    if amount < 0 or not 0 < ex_index <= len(closes):
        raise ValueError("amount must be non-negative and ex_index inside the series")
    prior = closes[ex_index - 1]
    if prior <= 0:
        raise ValueError("prior close must be positive")
    factor = (prior - amount) / prior
    if factor <= 0:
        raise ValueError("dividend exceeds the prior close")
    return [c * factor if i < ex_index else c for i, c in enumerate(closes)]


# ---------------------------------------------------------------- asynchronous completion and session boxes (CP-0345, CP-0063)
def bars_completed_by(rows: Iterable[dict[str, Any]], t_utc: str) -> list[dict[str, Any]]:
    """Bars whose ``completed_at_utc`` is at or before ``t_utc``, in completion order (CP-0345).

    Intraday and weekly bars complete at different times. A bar is usable only once it has completed, so the
    result for any ``t`` contains no bar that finished later. Both timestamps must be timezone-aware.
    """
    cutoff = datetime.fromisoformat(normalize_timestamp(t_utc)[1])
    out: list[tuple[datetime, dict[str, Any]]] = []
    for row in rows:
        _ist, utc = normalize_timestamp(str(row["completed_at_utc"]))
        completed = datetime.fromisoformat(utc)
        if completed <= cutoff:
            out.append((completed, dict(row)))
    return [row for _ts, row in sorted(out, key=lambda pair: pair[0])]


def candidate_within_box(calendar: SessionCalendar, start: date, end: date, *, max_sessions: int) -> bool:
    """True when a candidate starts and ends on trading sessions and spans at most ``max_sessions`` (CP-0063)."""
    if end < start or max_sessions < 1:
        return False
    if not (calendar.is_trading(start) and calendar.is_trading(end)):
        return False
    return len(calendar.trading_days(start, end)) <= max_sessions
