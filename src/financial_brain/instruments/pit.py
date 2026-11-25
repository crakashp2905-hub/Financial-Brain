"""Point-in-time validators and flags for ETF quotes, iNAV and NAV series (checklist D2).

Everything here answers one question: *could a strategy have known this value at the moment it
decided?* A quote that arrived after the decision, an iNAV that is not comparable with the quote
beside it, or a NAV that its scheme had not yet published is refused or flagged, never silently used.

Nothing here fetches data. The validators take observed values and their timestamps, so they can be
run over archived bytes and over paper-run ledgers alike.

Thresholds (staleness, skew, move size) are parameters with no default that encodes a market claim:
the caller states them and the states are recorded with the result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum


class PitError(ValueError):
    """A timestamp or value that cannot be used at all (naive time, non-positive price)."""


def _aware(ts: datetime, what: str) -> datetime:
    if not isinstance(ts, datetime) or ts.tzinfo is None or ts.utcoffset() is None:
        raise PitError(f"{what} must be a timezone-aware datetime")
    return ts


def _positive(value: object, what: str) -> Decimal:
    if isinstance(value, bool):
        raise PitError(f"{what} must be a decimal, not a bool")
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    if not d.is_finite() or d <= 0:
        raise PitError(f"{what} must be a positive finite decimal, got {value!r}")
    return d


# ---------------------------------------------------------------- NAV availability (FB-0308)

def nav_usable(published_at: datetime, decision_at: datetime) -> bool:
    """A NAV is usable only once its publication instant is at or before the decision instant.

    The NAV's own date is irrelevant here: a same-day NAV published after a pre-cut-off decision is
    not usable for that decision, even though it carries today's date.
    """
    return _aware(published_at, "published_at") <= _aware(decision_at, "decision_at")


# ------------------------------------------------------------- NAV row flags (FB-0318)

class NavFlag(str, Enum):
    FUTURE_DATED = "FUTURE_DATED"          # NAV date after the file it came in: impossible, refuse
    STALE = "STALE"                        # NAV date well behind the file: scheme not reporting
    LARGE_MOVE = "LARGE_MOVE"              # day-on-day move above threshold: check corporate action


def nav_row_flags(nav_date: date, file_date: date, *, max_lag_days: int) -> list[NavFlag]:
    """Flags for one NAV row against the date of the file that carried it.

    The row keeps its own date; the flags describe its freshness, they do not move it.
    """
    if max_lag_days < 0:
        raise PitError("max_lag_days must be non-negative")
    if nav_date > file_date:
        return [NavFlag.FUTURE_DATED]
    if (file_date - nav_date) > timedelta(days=max_lag_days):
        return [NavFlag.STALE]
    return []


@dataclass(frozen=True)
class NavMove:
    on: date
    previous: date
    change: Decimal
    flag: NavFlag


def nav_series_moves(series: list[tuple[date, object]], *, max_move: object) -> list[NavMove]:
    """Day-on-day NAV moves beyond ``max_move`` (a fraction, e.g. 0.2 for 20%).

    The series must be strictly increasing by date. A large move is not an error: it is flagged so
    that a corporate action (split, IDCW, merger) can be matched to it, or the move is rejected as
    data error before any return is computed from it.
    """
    limit = _positive(max_move, "max_move")
    out: list[NavMove] = []
    prev: tuple[date, Decimal] | None = None
    for on, value in series:
        v = _positive(value, "NAV")
        if prev is not None:
            if on <= prev[0]:
                raise PitError(f"NAV series not strictly increasing at {on}")
            change = (v - prev[1]) / prev[1]
            if abs(change) > limit:
                out.append(NavMove(on, prev[0], change, NavFlag.LARGE_MOVE))
        prev = (on, v)
    return out


# ----------------------------------------------------------------- ETF quotes (FB-0205, FB-0214)

@dataclass(frozen=True)
class Quote:
    """A top-of-book observation. ``exchange_ts`` is the exchange's stamp; ``received_ts`` is ours."""

    bid: Decimal
    ask: Decimal
    exchange_ts: datetime
    received_ts: datetime
    bid_qty: int = 0
    ask_qty: int = 0


class PriceStatus(str, Enum):
    QUOTE_BACKED = "QUOTE_BACKED"   # an executable bid/ask was observed in time
    UNPRICED = "UNPRICED"           # no usable quote; must never be reported as filled


@dataclass(frozen=True)
class Pricing:
    status: PriceStatus
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.status is PriceStatus.QUOTE_BACKED


def quote_at(quote: Quote | None, decision_at: datetime, *, max_age: timedelta) -> Pricing:
    """Whether a quote was known at ``decision_at`` and is fresh enough to price a fill.

    Refused, with reasons, when: no quote; the quote was received after the decision; the exchange
    stamp is older than ``max_age``; the book is crossed or locked; a side has no size. The daily
    close and same-day NAV are never substituted for a missing quote: that case is UNPRICED.
    """
    decision_at = _aware(decision_at, "decision_at")
    if quote is None:
        return Pricing(PriceStatus.UNPRICED, ("no quote",))
    _aware(quote.exchange_ts, "exchange_ts")
    _aware(quote.received_ts, "received_ts")
    reasons: list[str] = []
    if quote.received_ts > decision_at:
        reasons.append("quote received after the decision")
    if decision_at - quote.exchange_ts > max_age:
        reasons.append(f"quote older than {max_age}")
    if quote.bid <= 0 or quote.ask <= 0:
        reasons.append("bid or ask missing")
    elif quote.bid > quote.ask:
        reasons.append("crossed book")
    elif quote.bid == quote.ask:
        reasons.append("locked book")
    if quote.bid_qty <= 0 or quote.ask_qty <= 0:
        reasons.append("no size on one side")
    if reasons:
        return Pricing(PriceStatus.UNPRICED, tuple(reasons))
    return Pricing(PriceStatus.QUOTE_BACKED)


def fill_reference(quote: Quote, side: str) -> Decimal:
    """Executable reference price: a buy lifts the ask, a sell hits the bid. Never the mid."""
    if side == "BUY":
        return quote.ask
    if side == "SELL":
        return quote.bid
    raise PitError(f"side must be BUY or SELL, got {side!r}")


# -------------------------------------------------------------------- iNAV (FB-0207, FB-0210)

@dataclass(frozen=True)
class Inav:
    value: Decimal
    as_of: datetime


@dataclass(frozen=True)
class Premium:
    """Premium (positive) or discount (negative) of the quote mid against iNAV, as a fraction."""

    fraction: Decimal | None
    reason: str | None


def inav_premium(quote: Quote, inav: Inav | None, *, max_skew: timedelta,
                 max_inav_age: timedelta, decision_at: datetime) -> Premium:
    """Quote-versus-iNAV premium, refused when the two are not comparable in time.

    An iNAV whose stamp is more than ``max_skew`` from the quote's stamp is asynchronous: a
    premium computed from it would be an artefact of timing, so it is refused, not reported.
    """
    decision_at = _aware(decision_at, "decision_at")
    if inav is None:
        return Premium(None, "no iNAV")
    _aware(inav.as_of, "iNAV as_of")
    if decision_at - inav.as_of > max_inav_age:
        return Premium(None, "iNAV is stale")
    if abs(quote.exchange_ts - inav.as_of) > max_skew:
        return Premium(None, "iNAV and quote are not synchronous")
    value = _positive(inav.value, "iNAV")
    mid = (quote.bid + quote.ask) / 2
    return Premium(mid / value - 1, None)
