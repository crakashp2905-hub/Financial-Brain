"""Candlestick and gap primitives, weekly context and multi-timeframe alignment (stage 07).

- :data:`CANDLE_MAP` inventories the library's ``c_*`` features against catalogue IDs (CP-0108).
- :func:`library_candle_events` wraps those features as timed events and never
  re-implements them (CP-0109). The library only reads prior bars.
- Gap classification separates a body gap from a whole-range gap (CP-0111) and
  refuses gaps explained by corporate actions or circuit freezes (CP-0120).
- Definitions still pending in the source notes are listed and excluded from
  inference (CP-0114).
- Weekly bars are final only when the following week has begun (CP-0117).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

# Library column -> catalogue IDs it approximates. Compatibility notes are explicit.
CANDLE_MAP: dict[str, tuple[str, ...]] = {
    "c_doji": ("N18",),
    "c_hammer": ("N01",),
    "c_shooting_star": ("N02",),
    "c_bull_engulf": ("N03",),
    "c_bear_engulf": ("N03",),
    "c_dark_cloud": ("N04",),
    "c_piercing": ("N05",),
    "c_morning_star": ("N06",),
    "c_evening_star": ("N06",),
    "c_bull_harami": ("N08",),
    "c_marubozu": ("N10",),
    "c_three_soldiers": ("N13",),
    "c_three_crows": ("N13",),
}
COMPATIBILITY_NOTES: dict[str, str] = {
    "c_marubozu": "approximates belt hold (N10); a marubozu is not the same shape as a belt hold",
    "c_doji": "one of several doji families in N18; N07 doji-star is not separately modelled",
}
UNMAPPED_CATALOGUE_CANDLES = ("N07", "N09", "N11", "N12", "N14", "N15", "N16", "N17", "N19", "N20", "N21", "N22", "N23", "N24", "N25")

PENDING_DEFINITIONS = frozenset(
    {"tasuki gaps", "gapping plays", "side-by-side white lines", "separating lines", "rising three methods", "falling three methods"}
)

_CANDLE_DIRECTION = {
    "c_bull_engulf": "bullish",
    "c_bull_harami": "bullish",
    "c_piercing": "bullish",
    "c_morning_star": "bullish",
    "c_three_soldiers": "bullish",
    "c_bear_engulf": "bearish",
    "c_dark_cloud": "bearish",
    "c_evening_star": "bearish",
    "c_three_crows": "bearish",
}


@dataclass(frozen=True)
class CandleEvent:
    column: str
    index: int
    date: str
    direction: str
    catalogue_ids: tuple[str, ...]


def library_candle_events(frame: Any) -> list[CandleEvent]:
    """Timed events from the existing ``c_*`` library columns (CP-0109)."""
    from financial_brain.features import library  # existing, read-only feature library

    feats = library.compute(frame)
    events: list[CandleEvent] = []
    dates = [str(d) for d in frame["d"]]
    for column, ids in CANDLE_MAP.items():
        values = feats[column].tolist()
        for i, value in enumerate(values):
            if value is None or (isinstance(value, float) and math.isnan(value)) or value == 0:
                continue
            if column == "c_marubozu":
                direction = "bullish" if value > 0 else "bearish"
            elif column == "c_doji" or column == "c_hammer" or column == "c_shooting_star":
                direction = "neutral"  # descriptive shape; reversal side is not implied
            else:
                direction = _CANDLE_DIRECTION[column]
            events.append(CandleEvent(column, i, dates[i], direction, ids))
    return sorted(events, key=lambda e: (e.index, e.column))


def causal_check(frame: Any, cutoff: int) -> bool:
    """Candle features on a prefix equal the full run restricted to the cutoff bar."""
    from financial_brain.features import library

    full = library.compute(frame)
    part = library.compute(frame.iloc[: cutoff + 1].reset_index(drop=True))
    for column in CANDLE_MAP:
        a = full[column].iloc[: cutoff + 1].tolist()
        b = part[column].tolist()
        for x, y in zip(a, b, strict=True):
            both_nan = isinstance(x, float) and isinstance(y, float) and math.isnan(x) and math.isnan(y)
            if not both_nan and x != y:
                return False
    return True


def gap_kind(prev_high: float, prev_low: float, prev_open: float, prev_close: float,
             high: float, low: float, open_: float, close: float) -> str | None:
    """Classify a gap between consecutive bars (CP-0111).

    ``range_gap`` (Nison window): today's low is above yesterday's high (or the reverse).
    ``body_gap``: the bodies do not overlap but the ranges do.
    """
    prev_body_lo, prev_body_hi = min(prev_open, prev_close), max(prev_open, prev_close)
    body_lo, body_hi = min(open_, close), max(open_, close)
    if low > prev_high:
        return "range_gap_up"
    if high < prev_low:
        return "range_gap_down"
    if body_lo > prev_body_hi:
        return "body_gap_up"
    if body_hi < prev_body_lo:
        return "body_gap_down"
    return None


def classify_gap_with_exclusions(kind: str | None, *, corporate_action: bool, circuit_frozen: bool) -> str:
    """Gaps explained by a corporate action or a circuit freeze never become breakouts (CP-0120)."""
    if kind is None:
        return "NO_GAP"
    if corporate_action:
        return "EXCLUDED_CORPORATE_ACTION"
    if circuit_frozen:
        return "EXCLUDED_CIRCUIT_FREEZE"
    return kind


def is_inference_eligible(definition_name: str) -> bool:
    """Pending definitions stay out of any inference path (CP-0114)."""
    return definition_name.strip().lower() not in PENDING_DEFINITIONS


@dataclass(frozen=True)
class DailyBar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


def weekly_bars(days: list[DailyBar], *, week_end_seen: date | None) -> list[dict[str, Any]]:
    """Aggregate completed exchange sessions into ISO weeks (CP-0117).

    A week is ``final`` only when a session from a later ISO week exists in ``days``
    (or ``week_end_seen`` says the week has closed). A partial week is flagged.
    """
    buckets: dict[tuple[int, int], list[DailyBar]] = {}
    for bar in sorted(days, key=lambda b: b.day):
        iso = bar.day.isocalendar()
        buckets.setdefault((iso.year, iso.week), []).append(bar)
    keys = sorted(buckets)
    out: list[dict[str, Any]] = []
    for key in keys:
        items = buckets[key]
        later_exists = any((b.day.isocalendar().year, b.day.isocalendar().week) > key for b in days)
        closed = later_exists or (week_end_seen is not None and items[-1].day >= week_end_seen)
        out.append(
            {
                "iso_week": f"{key[0]}-W{key[1]:02d}",
                "open": items[0].open,
                "high": max(b.high for b in items),
                "low": min(b.low for b in items),
                "close": items[-1].close,
                "volume": sum(b.volume for b in items),
                "sessions": len(items),
                "status": "FINAL" if closed else "PARTIAL",
                "known_at_day": items[-1].day.isoformat(),
            }
        )
    return out


def mtf_known_at_monotonic(known_at_days: list[date]) -> bool:
    """Known-at timestamps must never move backwards in a multi-timeframe stream (CP-0118)."""
    return all(a <= b for a, b in zip(known_at_days, known_at_days[1:], strict=False))


K7_NOTICE = (
    "K7 (chart/candlestick futures setups) failed its preregistered G3 gate. "
    "This result is recorded, not reinterpreted, and is not evidence of a working pattern."
)


def final_weeks(weeks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only FINAL weeks may feed a signal; partial weeks never masquerade as final (CP-0139, CP-0117)."""
    return [w for w in weeks if w["status"] == "FINAL"]
