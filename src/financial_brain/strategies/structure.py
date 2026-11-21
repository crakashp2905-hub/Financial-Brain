"""Causal structure: confirmed HH/HL/LH/LL labels, trend state, higher-timeframe completeness, pivot marks, dedupe,
scale-free swing significance and structure-state labels (ALG-020, ALG-023..032).

Labels use confirmed pivots only. A pivot becomes usable at ``index + right`` (see
:mod:`financial_brain.patterns.pivots`), so label sequences are prefix-invariant by construction and are tested
that way.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from financial_brain.patterns.pivots import Pivot, causal_pivots


@dataclass(frozen=True)
class PivotMark:
    """A pivot plotted at its original bar, with the bar at which it became knowable (ALG-031)."""

    index: int
    kind: str
    price: float
    recognised_at: int


def pivot_marks(highs: Sequence[float], lows: Sequence[float], *, left: int = 2, right: int = 2) -> list[PivotMark]:
    return [PivotMark(p.index, p.kind, p.price, p.known_at_index) for p in causal_pivots(highs, lows, left=left, right=right)]


@dataclass(frozen=True)
class StructureLabel:
    index: int
    label: str  # HH, HL, LH, LL
    known_at: int


def structure_labels(pivots: Sequence[Pivot], *, upto: int | None = None) -> list[StructureLabel]:
    """HH/HL/LH/LL for each confirmed pivot, comparing it to the previous pivot of the same kind (ALG-025).

    ``upto`` limits the pivots to those known by that bar, so the result for a prefix equals the prefix of the result.
    """
    known = [p for p in pivots if upto is None or p.known_at_index <= upto]
    known.sort(key=lambda p: p.index)
    out: list[StructureLabel] = []
    last_high: Pivot | None = None
    last_low: Pivot | None = None
    for p in known:
        if p.kind == "high":
            if last_high is not None:
                out.append(StructureLabel(p.index, "HH" if p.price > last_high.price else "LH", p.known_at_index))
            last_high = p
        else:
            if last_low is not None:
                out.append(StructureLabel(p.index, "HL" if p.price > last_low.price else "LL", p.known_at_index))
            last_low = p
    return out


def trend_state(labels: Sequence[StructureLabel]) -> str:
    """UPTREND when the latest two highs and lows are both rising, DOWNTREND when both fall, else RANGE (ALG-026)."""
    highs = [x.label for x in labels if x.label in ("HH", "LH")]
    lows = [x.label for x in labels if x.label in ("HL", "LL")]
    if len(highs) < 1 or len(lows) < 1:
        return "RANGE"
    if highs[-1] == "HH" and lows[-1] == "HL":
        return "UPTREND"
    if highs[-1] == "LH" and lows[-1] == "LL":
        return "DOWNTREND"
    return "RANGE"


def higher_timeframe_known(htf_close_index: int, signal_index: int) -> bool:
    """A higher-timeframe bar is usable only once it has closed before the signal bar (ALG-020, ALG-026)."""
    return htf_close_index < signal_index


def weekly_completed(daily: Iterable[tuple[date, float]], as_of: date) -> list[tuple[str, float]]:
    """Weekly closes for weeks whose Friday has been reached by ``as_of`` (ALG-027).

    A week that has not reached its Friday is never reported, so no partial week leaks into a higher-timeframe state.
    """
    by_week: dict[tuple[int, int], list[tuple[date, float]]] = {}
    for d, c in daily:
        if d > as_of:
            continue
        by_week.setdefault(d.isocalendar()[:2], []).append((d, c))
    out: list[tuple[str, float]] = []
    for (year, week), rows in sorted(by_week.items()):
        friday = date.fromisocalendar(year, week, 5)
        if friday > as_of:
            continue
        out.append((f"{year}-W{week:02d}", max(rows)[1]))
    return out


def dedupe_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One event per security lineage, timeframe and pivot identity; replays keep the first copy (ALG-029, ALG-041)."""
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for e in events:
        key = (e["lineage"], e["timeframe"], tuple(e["pivot_indices"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def significant_swing(prev_price: float, price: float, atr: float, *, min_atr: float = 1.0) -> bool:
    """A move counts only when it is at least ``min_atr`` times ATR. Dimensionless, so scale does not change it (ALG-024)."""
    if atr <= 0:
        return False
    return abs(price - prev_price) >= min_atr * atr


def structure_state(closes: Sequence[float], gap_threshold: float = 0.05) -> str:
    """Flat, ranging, volatile or gapping labels for synthetic sequences (ALG-032)."""
    if len(closes) < 3:
        return "INSUFFICIENT"
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    if any(abs(r) >= gap_threshold for r in rets):
        return "GAPPING"
    spread = max(closes) - min(closes)
    if spread == 0:
        return "FLAT"
    if spread / closes[0] < 0.01:
        return "RANGING"
    if max(abs(r) for r in rets) > 0.02:
        return "VOLATILE"
    return "RANGING"


def require_prior_trend(formations: Iterable[Any], closes: Sequence[float], *, direction: str, lookback: int = 10) -> list[Any]:
    """Keep only formations whose prior ``lookback`` closes show the required trend (ALG-033, ALG-034).

    A double top needs a prior uptrend; a double bottom needs a prior downtrend. The test compares the close at the
    start of the formation with the close ``lookback`` bars earlier, so it uses only bars before the formation.
    """
    if direction not in ("UP", "DOWN"):
        raise ValueError("direction must be UP or DOWN")
    kept: list[Any] = []
    for f in formations:
        start = int(f.start_index)
        if start - lookback < 0:
            continue
        before, at = closes[start - lookback], closes[start]
        if direction == "UP" and at > before:
            kept.append(f)
        elif direction == "DOWN" and at < before:
            kept.append(f)
    return kept


def formation_denominator(formations: Iterable[Any], *, now: int) -> dict[str, int]:
    """Formed, confirmed, failed and still-open counts from one report (ALG-040). Rejected lookalikes stay in ``formed``."""
    counts = {"formed": 0, "confirmed": 0, "failed": 0, "open": 0}
    for f in formations:
        if f.candidate_known_index > now:
            continue
        counts["formed"] += 1
        if f.failed_index is not None and f.failed_index <= now:
            counts["failed"] += 1
        elif f.confirmed_index is not None and f.confirmed_index <= now:
            counts["confirmed"] += 1
        else:
            counts["open"] += 1
    return counts
