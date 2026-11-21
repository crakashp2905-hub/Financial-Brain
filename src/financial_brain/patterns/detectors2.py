"""Wave 9 causal detectors and state helpers (CP-0081..0106, CP-0113, CP-0115, CP-0116, CP-0104, CP-0105).

Same causality contract as :mod:`financial_brain.patterns.detectors`: a candidate is decided only from
bars ``<= e`` where ``e`` is its ``candidate_known_index``, and is emitted only on the bar that makes its
last input knowable (``known == e``). Confirmation and failure are searched after ``e``.

Every candidate keeps ``direction == "neutral"``. No function here returns a profit target: levels are
explanatory (CP-0105). Detectors are research descriptions, not trading signals.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import numpy as np

from financial_brain.patterns.detectors import (
    VERSION,
    Bars,
    Formation,
    _const,
    _line,
    _resolve,
    atr,
    flags,
)
from financial_brain.patterns.geometry import Line
from financial_brain.patterns.pivots import causal_pivots

MAX_PIVOTS = 6  # CP-0106: more free pivots than this makes a shape too flexible to mean anything


def pivot_count(geometry: dict[str, Any]) -> int:
    """Number of geometry points a formation is built from (pivots, extrema, lips, and so on)."""
    count = 0
    for key in ("pivots", "extrema"):
        value = geometry.get(key)
        if isinstance(value, list):
            count += len(value)
    count += sum(1 for key in ("left_lip", "cup_low", "right_lip") if key in geometry)
    return count


def within_complexity_cap(f: Formation, max_pivots: int = MAX_PIVOTS) -> bool:
    """True when the formation uses no more than ``max_pivots`` geometry points (CP-0106)."""
    return pivot_count(f.geometry) <= max_pivots


def _slopes(line_hi: Line, line_lo: Line, mean: float) -> tuple[float, float]:
    return line_hi.slope / mean, line_lo.slope / mean


# ---------------------------------------------------------------- continuation: pennant and channel
def pennant(
    bars: Bars, *, pole_len: int = 8, pole_min_ret: float = 0.08, window: int = 18, left: int = 1, right: int = 1,
    taper: float = 0.7,
) -> list[Formation]:
    """A short symmetric taper (bounded by ``window``) that follows a sharp pole (CP-0081)."""
    out: list[Formation] = []
    seen: set[tuple[Any, ...]] = set()
    all_p = causal_pivots(bars.high, bars.low, left=left, right=right)
    for e in range(window + pole_len, len(bars)):
        a = atr(bars, e)
        if a is None:
            continue
        start = e - window + 1
        pole_end = start - 1
        pole_start = pole_end - pole_len
        ret = (bars.close[pole_end] - bars.close[pole_start]) / bars.close[pole_start]
        if abs(ret) < pole_min_ret:
            continue
        pivots = [p for p in all_p if p.known_at_index <= e and p.index >= start]
        highs = [p for p in pivots if p.kind == "high"]
        lows = [p for p in pivots if p.kind == "low"]
        if len(highs) < 2 or len(lows) < 2:
            continue
        h0, h1, l0, l1 = highs[0], highs[-1], lows[0], lows[-1]
        line_hi = Line.through(h0.index, h0.price, h1.index, h1.price)
        line_lo = Line.through(l0.index, l0.price, l1.index, l1.price)
        if not (line_hi.slope < 0 < line_lo.slope):
            continue  # a pennant is a symmetric taper: upper line falls, lower line rises
        width0 = line_hi.at(start) - line_lo.at(start)
        width1 = line_hi.at(e) - line_lo.at(e)
        if width0 <= 0 or width1 <= 0 or width1 > taper * width0:
            continue
        known = max(p.known_at_index for p in (h0, h1, l0, l1))
        if known != e:
            continue
        direction = "bullish" if ret > 0 else "bearish"
        key = (direction, pole_end, h0.index, h1.index, l0.index, l1.index)
        if key in seen:
            continue
        seen.add(key)
        res = _resolve(bars, known, up=_line(line_hi)) if direction == "bullish" else _resolve(
            bars, known, down=_line(line_lo)
        )
        out.append(
            Formation(
                f"pennant_{direction}", VERSION, pole_start, known,
                confirmed_index=res.get("confirmed_index"),
                confirmed_direction=res.get("confirmed_direction"),
                failed_index=res.get("failed_index"),
                geometry={"pole_start": pole_start, "pole_end": pole_end, "window": window,
                          "pivots": [h0.index, h1.index, l0.index, l1.index]},
            )
        )
    return out


def channels(
    bars: Bars, *, window: int = 40, left: int = 2, right: int = 2, flat_tol: float = 0.0005, parallel_tol: float = 0.25
) -> list[Formation]:
    """Rising or falling parallel boundaries from known pivots (CP-0083). Parallel lines are not evidence of an edge."""
    out: list[Formation] = []
    seen: set[tuple[Any, ...]] = set()
    all_p = causal_pivots(bars.high, bars.low, left=left, right=right)
    for e in range(window, len(bars)):
        pivots = [p for p in all_p if p.known_at_index <= e and p.index >= e - window + 1]
        highs = [p for p in pivots if p.kind == "high"]
        lows = [p for p in pivots if p.kind == "low"]
        if len(highs) < 2 or len(lows) < 2:
            continue
        h0, h1, l0, l1 = highs[0], highs[-1], lows[0], lows[-1]
        mean = sum(bars.close[e - window + 1 : e + 1]) / window
        line_hi = Line.through(h0.index, h0.price, h1.index, h1.price)
        line_lo = Line.through(l0.index, l0.price, l1.index, l1.price)
        s_hi, s_lo = _slopes(line_hi, line_lo, mean)
        kind: str | None = None
        if s_hi > flat_tol and s_lo > flat_tol and abs(s_hi - s_lo) <= parallel_tol * max(s_hi, s_lo):
            kind = "channel_rising"
        elif s_hi < -flat_tol and s_lo < -flat_tol and abs(s_hi - s_lo) <= parallel_tol * max(-s_hi, -s_lo):
            kind = "channel_falling"
        if kind is None:
            continue
        known = max(p.known_at_index for p in (h0, h1, l0, l1))
        if known != e:
            continue
        key = (kind, h0.index, h1.index, l0.index, l1.index)
        if key in seen:
            continue
        seen.add(key)
        res = _resolve(bars, known, up=_line(line_hi), down=_line(line_lo))
        out.append(
            Formation(
                kind, VERSION, e - window + 1, known,
                confirmed_index=res.get("confirmed_index"),
                confirmed_direction=res.get("confirmed_direction"),
                failed_index=res.get("failed_index"),
                geometry={"pivots": [h0.index, h1.index, l0.index, l1.index], "window": window},
            )
        )
    return out


# ---------------------------------------------------------------- cup, handle, high-tight flag
def cup_handle(
    bars: Bars, *, window: int = 80, left: int = 2, right: int = 2, min_cup: int = 15, lip_tol_atr: float = 0.75,
    min_depth: float = 0.12, max_depth: float = 0.5, handle_len: int = 8,
) -> list[Formation]:
    """Left lip, cup low, right lip, then a shallow handle of fixed length (CP-0084).

    One candidate per right lip, emitted ``handle_len`` bars after that lip. The handle may not dip below
    half the cup depth and may not exceed the right lip.
    """
    out: list[Formation] = []
    all_p = causal_pivots(bars.high, bars.low, left=left, right=right)
    n = len(bars)
    for r in all_p:
        if r.kind != "high":
            continue
        e = r.index + handle_len
        if e >= n or r.known_at_index > e:
            continue
        a = atr(bars, e)
        if a is None:
            continue
        lips = [
            p for p in all_p
            if p.kind == "high" and r.index - window <= p.index <= r.index - min_cup and p.known_at_index <= e
        ]
        if not lips:
            continue
        lip = max(lips, key=lambda p: (p.price, -p.index))
        if abs(lip.price - r.price) > lip_tol_atr * a:
            continue
        lows = [p for p in all_p if p.kind == "low" and lip.index < p.index < r.index and p.known_at_index <= e]
        if not lows:
            continue
        cup = min(lows, key=lambda p: (p.price, p.index))
        base = min(lip.price, r.price)
        depth = (base - cup.price) / base
        if not min_depth <= depth <= max_depth:
            continue
        handle = range(r.index + 1, e + 1)
        if min(bars.low[k] for k in handle) < cup.price + 0.5 * (r.price - cup.price):
            continue
        if max(bars.high[k] for k in handle) > r.price:
            continue
        res = _resolve(bars, e, up=_const(r.price), fail_down=cup.price)
        out.append(
            Formation(
                "cup_with_handle", VERSION, lip.index, e,
                confirmed_index=res.get("confirmed_index"),
                confirmed_direction=res.get("confirmed_direction"),
                failed_index=res.get("failed_index"),
                geometry={"left_lip": lip.index, "cup_low": cup.index, "right_lip": r.index,
                          "handle_end": e, "depth": round(depth, 6), "neckline": r.price},
            )
        )
    return out


def high_tight_flag(bars: Bars) -> list[Formation]:
    """Historical high-rise (>= 80% in at most 40 bars) followed by a flag of 5 to 20 bars retracing at most 25% (CP-0085)."""
    return [
        replace(f, detector="high_tight_flag")
        for f in flags(bars, pole_len=40, pole_min_ret=0.8, flag_min=5, flag_max=20, max_retrace=0.25)
        if f.detector == "flag_bullish"
    ]


# ---------------------------------------------------------------- reversal and complex shapes
def triple_extrema(
    bars: Bars, *, window: int = 90, left: int = 2, right: int = 2, min_sep: int = 4, tol_atr: float = 0.75,
    max_ratio: float = 2.5,
) -> list[Formation]:
    """Three equal extrema with bounded spacing asymmetry and a shared neckline (CP-0094)."""
    out: list[Formation] = []
    seen: set[tuple[Any, ...]] = set()
    all_p = causal_pivots(bars.high, bars.low, left=left, right=right)
    for e in range(window, len(bars)):
        a = atr(bars, e)
        if a is None:
            continue
        pivots = [p for p in all_p if p.known_at_index <= e and p.index >= e - window]
        for kind in ("low", "high"):
            pts = [p for p in pivots if p.kind == kind]
            for p1, p2, p3 in zip(pts, pts[1:], pts[2:], strict=False):
                if p3.known_at_index != e:
                    continue
                gaps = (p2.index - p1.index, p3.index - p2.index)
                if min(gaps) < min_sep or max(gaps) / min(gaps) > max_ratio:
                    continue
                prices = [p1.price, p2.price, p3.price]
                mean = sum(prices) / 3
                if max(abs(x - mean) for x in prices) > tol_atr * a:
                    continue
                key = (kind, p1.index, p2.index, p3.index)
                if key in seen:
                    continue
                seen.add(key)
                span = range(p1.index, p3.index + 1)
                if kind == "low":
                    neck = max(bars.high[k] for k in span)
                    res = _resolve(bars, e, up=_const(neck), fail_down=min(prices))
                    name = "triple_bottom"
                else:
                    neck = min(bars.low[k] for k in span)
                    res = _resolve(bars, e, down=_const(neck), fail_up=max(prices))
                    name = "triple_top"
                out.append(
                    Formation(
                        name, VERSION, p1.index, e,
                        confirmed_index=res.get("confirmed_index"),
                        confirmed_direction=res.get("confirmed_direction"),
                        failed_index=res.get("failed_index"),
                        geometry={"extrema": [p1.index, p2.index, p3.index], "neckline": neck},
                    )
                )
    return out


def rounding(bars: Bars, *, window: int = 60, min_r2: float = 0.8, min_depth: float = 0.05) -> list[Formation]:
    """Quadratic-fit rounding bottom or top with a predeclared fit and depth criterion (CP-0097).

    A formation is emitted on the first bar where the criterion becomes true, so each rounding is reported once.
    """
    out: list[Formation] = []
    x = np.arange(window, dtype=float)
    mid = (window - 1) / 2
    prev: str | None = None
    for e in range(window - 1, len(bars)):
        y = np.asarray(bars.close[e - window + 1 : e + 1], dtype=float)
        a_, b_, _c = np.polyfit(x, y, 2)
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - float(np.sum((y - np.polyval([a_, b_, _c], x)) ** 2)) / ss_tot if ss_tot > 0 else 0.0
        kind: str | None = None
        if a_ != 0 and r2 >= min_r2 and abs(-b_ / (2 * a_) - mid) <= 0.2 * window:
            edge_hi = max(y[0], y[-1])
            edge_lo = min(y[0], y[-1])
            if a_ > 0 and (edge_hi - y.min()) / y.min() >= min_depth:
                kind = "rounding_bottom"
            elif a_ < 0 and (y.max() - edge_lo) / edge_lo >= min_depth:
                kind = "rounding_top"
        if kind is not None and kind != prev:
            start = e - window + 1
            if kind == "rounding_bottom":
                res = _resolve(bars, e, up=_const(max(bars.high[start : e + 1])))
            else:
                res = _resolve(bars, e, down=_const(min(bars.low[start : e + 1])))
            out.append(
                Formation(
                    kind, VERSION, start, e,
                    confirmed_index=res.get("confirmed_index"),
                    confirmed_direction=res.get("confirmed_direction"),
                    failed_index=res.get("failed_index"),
                    geometry={"window": window, "r2": round(r2, 6)},
                )
            )
        prev = kind
    return out


def broadening(
    bars: Bars, *, window: int = 50, left: int = 1, right: int = 1, flat_tol: float = 0.0005
) -> list[Formation]:
    """Diverging known swing bands (CP-0100). The name records the trailing move, not a forecast."""
    out: list[Formation] = []
    seen: set[tuple[Any, ...]] = set()
    all_p = causal_pivots(bars.high, bars.low, left=left, right=right)
    for e in range(window, len(bars)):
        pivots = [p for p in all_p if p.known_at_index <= e and p.index >= e - window + 1]
        highs = [p for p in pivots if p.kind == "high"]
        lows = [p for p in pivots if p.kind == "low"]
        if len(highs) < 2 or len(lows) < 2:
            continue
        h0, h1, l0, l1 = highs[0], highs[-1], lows[0], lows[-1]
        mean = sum(bars.close[e - window + 1 : e + 1]) / window
        line_hi = Line.through(h0.index, h0.price, h1.index, h1.price)
        line_lo = Line.through(l0.index, l0.price, l1.index, l1.price)
        s_hi, s_lo = _slopes(line_hi, line_lo, mean)
        if not (s_hi > flat_tol and s_lo < -flat_tol):
            continue
        known = max(p.known_at_index for p in (h0, h1, l0, l1))
        if known != e:
            continue
        key = (h0.index, h1.index, l0.index, l1.index)
        if key in seen:
            continue
        seen.add(key)
        kind = "broadening_top" if bars.close[e] > bars.close[e - window] else "broadening_bottom"
        res = _resolve(bars, known, up=_line(line_hi), down=_line(line_lo))
        out.append(
            Formation(
                kind, VERSION, e - window + 1, known,
                confirmed_index=res.get("confirmed_index"),
                confirmed_direction=res.get("confirmed_direction"),
                failed_index=res.get("failed_index"),
                geometry={"pivots": [h0.index, h1.index, l0.index, l1.index]},
            )
        )
    return out


def diamond(bars: Bars, *, window: int = 60, left: int = 1, right: int = 1) -> list[Formation]:
    """Widening then narrowing bands: two pivots per side in each half (CP-0101). Candidate only, never confirmed here."""
    out: list[Formation] = []
    seen: set[tuple[Any, ...]] = set()
    all_p = causal_pivots(bars.high, bars.low, left=left, right=right)
    for e in range(window, len(bars)):
        pivots = [p for p in all_p if p.known_at_index <= e and p.index >= e - window + 1]
        half = e - window // 2
        h_a = [p for p in pivots if p.kind == "high" and p.index < half]
        h_b = [p for p in pivots if p.kind == "high" and p.index >= half]
        l_a = [p for p in pivots if p.kind == "low" and p.index < half]
        l_b = [p for p in pivots if p.kind == "low" and p.index >= half]
        if min(len(h_a), len(h_b), len(l_a), len(l_b)) < 2:
            continue
        ha0, ha1 = h_a[0], h_a[-1]
        la0, la1 = l_a[0], l_a[-1]
        hb0, hb1 = h_b[0], h_b[-1]
        lb0, lb1 = l_b[0], l_b[-1]
        widen = Line.through(ha0.index, ha0.price, ha1.index, ha1.price).slope > 0 and (
            Line.through(la0.index, la0.price, la1.index, la1.price).slope < 0
        )
        narrow = Line.through(hb0.index, hb0.price, hb1.index, hb1.price).slope < 0 and (
            Line.through(lb0.index, lb0.price, lb1.index, lb1.price).slope > 0
        )
        if not (widen and narrow):
            continue
        used = [ha0, ha1, la0, la1, hb0, hb1, lb0, lb1]
        known = max(p.known_at_index for p in used)
        if known != e:
            continue
        key = tuple(p.index for p in used)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            Formation(
                "diamond_candidate", VERSION, ha0.index, known,
                geometry={"pivots": [p.index for p in used]},
            )
        )
    return out


def island_reversal(bars: Bars, *, max_island: int = 5) -> list[Formation]:
    """A completed, non-overlapping island between two opposite gaps (CP-0102)."""
    out: list[Formation] = []
    n = len(bars)
    for j in range(2, n):
        for k in range(1, max_island + 1):
            i = j - k
            if i < 1:
                break
            island_low = min(bars.low[i:j])
            island_high = max(bars.high[i:j])
            if (
                bars.low[i] > bars.high[i - 1]
                and all(bars.low[m] > bars.high[i - 1] for m in range(i, j))
                and bars.high[j] < island_low
            ):
                res = _resolve(bars, j, down=_const(island_low), fail_up=island_high)
                out.append(_island(bars, "island_reversal_top", i, j, island_low, island_high, res))
                break
            if (
                bars.high[i] < bars.low[i - 1]
                and all(bars.high[m] < bars.low[i - 1] for m in range(i, j))
                and bars.low[j] > island_high
            ):
                res = _resolve(bars, j, up=_const(island_high), fail_down=island_low)
                out.append(_island(bars, "island_reversal_bottom", i, j, island_low, island_high, res))
                break
    return out


def _island(
    bars: Bars, name: str, i: int, j: int, lo: float, hi: float, res: dict[str, Any]
) -> Formation:
    return Formation(
        name, VERSION, i - 1, j,
        confirmed_index=res.get("confirmed_index"),
        confirmed_direction=res.get("confirmed_direction"),
        failed_index=res.get("failed_index"),
        geometry={"gap_in": i, "gap_out": j, "island_low": lo, "island_high": hi},
    )


def three_methods(bars: Bars, *, min_body_atr: float = 1.0) -> list[Formation]:
    """Three counter-trend bars inside a strong first candle, then a continuation close (CP-0113).

    Candidate on the third small bar. Continuation confirms only on a later close beyond the first candle.
    """
    out: list[Formation] = []
    for e in range(5, len(bars)):
        i = e - 3
        a = atr(bars, i - 1)  # the first candle is judged against the average range before it
        if a is None:
            continue
        body = bars.close[i] - bars.open[i]
        if abs(body) < min_body_atr * a:
            continue
        bullish = body > 0
        if not all(bars.high[m] <= bars.high[i] and bars.low[m] >= bars.low[i] for m in range(i + 1, e + 1)):
            continue
        if bullish and not bars.close[e] <= bars.close[i + 1]:
            continue  # counter-trend drift: the small bars must not close back up
        if not bullish and not bars.close[e] >= bars.close[i + 1]:
            continue
        if bullish:
            res = _resolve(bars, e, up=_const(bars.close[i]), fail_down=bars.low[i])
            name = "three_methods_bullish"
        else:
            res = _resolve(bars, e, down=_const(bars.close[i]), fail_up=bars.high[i])
            name = "three_methods_bearish"
        out.append(
            Formation(
                name, VERSION, i, e,
                confirmed_index=res.get("confirmed_index"),
                confirmed_direction=res.get("confirmed_direction"),
                failed_index=res.get("failed_index"),
                geometry={"first_bar": i, "last_bar": e},
            )
        )
    return out


def failed_gaps(bars: Bars, *, min_gap_frac: float = 0.005) -> list[Formation]:
    """Unfilled gaps (CP-0116). The fill bar is stored as ``failed_index``, so it is hidden until it has happened."""
    out: list[Formation] = []
    n = len(bars)
    for i in range(1, n):
        if bars.low[i] > bars.high[i - 1] and (bars.low[i] - bars.high[i - 1]) / bars.close[i - 1] >= min_gap_frac:
            level = bars.high[i - 1]
            fill = next((j for j in range(i + 1, n) if bars.low[j] <= level), None)
            out.append(
                Formation("gap_up", VERSION, i - 1, i, failed_index=fill,
                          geometry={"gap_low": level, "gap_high": bars.low[i], "outcome": "gap_filled" if fill else "open"})
            )
        elif bars.high[i] < bars.low[i - 1] and (bars.low[i - 1] - bars.high[i]) / bars.close[i - 1] >= min_gap_frac:
            level = bars.low[i - 1]
            fill = next((j for j in range(i + 1, n) if bars.high[j] >= level), None)
            out.append(
                Formation("gap_down", VERSION, i - 1, i, failed_index=fill,
                          geometry={"gap_low": bars.high[i], "gap_high": level, "outcome": "gap_filled" if fill else "open"})
            )
    return out


# ---------------------------------------------------------------- state helpers
def throwback_state(
    bars: Bars, f: Formation, level: float, t: int, *, max_bars: int = 10, tol_frac: float = 0.01
) -> str | None:
    """Throwback or pullback state after a confirmed breakout, as knowable at bar ``t`` (CP-0104).

    Bullish: a return to ``level`` that closes back above it is ``THROWBACK_HELD``; a close more than
    ``tol_frac`` below it is ``THROWBACK_FAILED``. Bearish is mirrored. Only bars ``<= t`` are read.
    """
    if f.confirmed_index is None or f.confirmed_index > t or f.confirmed_direction not in ("bullish", "bearish"):
        return None
    bullish = f.confirmed_direction == "bullish"
    last = min(t, f.confirmed_index + max_bars)
    touched = False
    for j in range(f.confirmed_index + 1, last + 1):
        if bullish:
            if bars.close[j] < level * (1 - tol_frac):
                return "THROWBACK_FAILED"
            if bars.low[j] <= level * (1 + tol_frac):
                touched = True
                if bars.close[j] > level:
                    return "THROWBACK_HELD"
        else:
            if bars.close[j] > level * (1 + tol_frac):
                return "THROWBACK_FAILED"
            if bars.high[j] >= level * (1 - tol_frac):
                touched = True
                if bars.close[j] < level:
                    return "THROWBACK_HELD"
    return "PULLBACK_TOUCHED" if touched else None


def levels(f: Formation) -> dict[str, Any]:
    """Explanatory overlay levels (CP-0105). ``profit_target`` is always ``None``."""
    g = f.geometry
    breakout = g.get("top", g.get("neckline"))
    invalidation = g.get("bottom", g.get("fail_level"))
    return {
        "breakout_level": breakout,
        "invalidation_level": invalidation,
        "profit_target": None,
        "target_policy": "not_modelled",
    }


def sr_confluence(bars: Bars, level: float, t: int, *, tol_atr: float = 0.5, left: int = 2, right: int = 2) -> dict[str, Any]:
    """Count known pivots near ``level`` at bar ``t`` (CP-0115). Observational only: no multiplier is applied."""
    prefix = bars.head(t + 1)
    a = atr(prefix, t)
    if a is None:
        return {"observational_only": True, "touches": 0, "multiplier": None}
    pivots = causal_pivots(prefix.high, prefix.low, left=left, right=right)
    touches = sum(1 for p in pivots if p.known_at_index <= t and abs(p.price - level) <= tol_atr * a)
    return {"observational_only": True, "touches": touches, "multiplier": None}


EXTENDED_DETECTORS: tuple[Callable[[Bars], list[Formation]], ...] = (
    pennant,
    channels,
    cup_handle,
    high_tight_flag,
    triple_extrema,
    rounding,
    broadening,
    diamond,
    island_reversal,
    three_methods,
    failed_gaps,
)


def discover_extended(bars: Bars) -> list[Formation]:
    """Wave 9 detectors in canonical order. Not wired into the scan pipeline yet (see the completion matrix)."""
    found = [f for detector in EXTENDED_DETECTORS for f in detector(bars)]
    return sorted(found, key=lambda f: (f.candidate_known_index, f.detector, f.start_index, str(f.geometry)))
