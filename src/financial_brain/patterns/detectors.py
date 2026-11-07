"""Causal chart-pattern detectors over completed bars (stages 05-06).

Causality contract (CP-0136/0137/0140): a candidate is decided only from bars
``<= e`` where ``e`` is its ``candidate_known_index``. Confirmation and failure
are searched *after* ``e`` and are stored as later-dated facts. The
:meth:`Formation.as_of` view hides any transition not yet knowable at ``t``, so
``detect(bars[:t+1])`` must equal ``[f.as_of(t) for f in detect(bars) if known by t]``.
That identity is the property every detector is tested against.

A candidate's ``direction`` is always ``neutral``. Only a confirmed breakout sets
``confirmed_direction`` (CP-0075). Failed candidates are kept, never dropped (CP-0124).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from financial_brain.patterns.pivots import causal_pivots
from financial_brain.patterns.geometry import Line

VERSION = "1"


class InvalidBars(ValueError):
    """A bar violates OHLC or finiteness rules."""


@dataclass(frozen=True)
class Bars:
    open: tuple[float, ...]
    high: tuple[float, ...]
    low: tuple[float, ...]
    close: tuple[float, ...]

    def __post_init__(self) -> None:
        n = len(self.close)
        if not (len(self.open) == len(self.high) == len(self.low) == n):
            raise InvalidBars("OHLC columns must have equal length")
        for i, (o, h, lo, c) in enumerate(zip(self.open, self.high, self.low, self.close, strict=True)):
            for name, v in (("open", o), ("high", h), ("low", lo), ("close", c)):
                if not math.isfinite(v) or v <= 0:
                    raise InvalidBars(f"bar {i}: {name} must be finite and positive")
            if h < lo or h < max(o, c) or lo > min(o, c):
                raise InvalidBars(f"bar {i}: OHLC inequality violated")

    def __len__(self) -> int:
        return len(self.close)

    def head(self, n: int) -> Bars:
        return Bars(self.open[:n], self.high[:n], self.low[:n], self.close[:n])

    @staticmethod
    def from_closes(closes: list[float], spread: float = 0.5) -> Bars:
        opens = [closes[0]] + closes[:-1]
        highs = [max(o, c) + spread for o, c in zip(opens, closes, strict=True)]
        lows = [min(o, c) - spread for o, c in zip(opens, closes, strict=True)]
        return Bars(tuple(opens), tuple(highs), tuple(lows), tuple(closes))


def quarantine_rows(rows: list[tuple[float, float, float, float]]) -> tuple[list[tuple[float, float, float, float]], dict[str, int]]:
    """Split raw (o, h, l, c) rows into valid rows and counted rejection reasons (CP-0042)."""
    good: list[tuple[float, float, float, float]] = []
    counts: dict[str, int] = {}
    for o, h, lo, c in rows:
        vals = (o, h, lo, c)
        if not all(math.isfinite(v) and v > 0 for v in vals):
            reason = "non_finite_or_nonpositive"
        elif h < lo or h < max(o, c) or lo > min(o, c):
            reason = "ohlc_inequality"
        else:
            good.append((o, h, lo, c))
            continue
        counts[reason] = counts.get(reason, 0) + 1
    return good, counts


def atr(bars: Bars, t: int, period: int = 14) -> float | None:
    """Average true range using bars ``<= t`` only (causal)."""
    if t < 1:
        return None
    start = max(1, t - period + 1)
    trs = []
    for k in range(start, t + 1):
        prev_c = bars.close[k - 1]
        trs.append(max(bars.high[k] - bars.low[k], abs(bars.high[k] - prev_c), abs(bars.low[k] - prev_c)))
    value = sum(trs) / len(trs)
    return value if value > 0 else None


@dataclass(frozen=True)
class Formation:
    detector: str
    version: str
    start_index: int
    candidate_known_index: int
    direction: str = "neutral"
    confirmed_index: int | None = None
    confirmed_direction: str | None = None
    failed_index: int | None = None
    geometry: dict[str, Any] = field(default_factory=dict)

    def state_at(self, t: int) -> str | None:
        if t < self.candidate_known_index:
            return None
        if self.failed_index is not None and self.failed_index <= t:
            return "FAILED"
        if self.confirmed_index is not None and self.confirmed_index <= t:
            return "CONFIRMED"
        return "CANDIDATE"

    def as_of(self, t: int) -> Formation | None:
        """The formation as knowable at bar ``t``; ``None`` if the candidate is not yet known."""
        if t < self.candidate_known_index:
            return None
        conf = self.confirmed_index if self.confirmed_index is not None and self.confirmed_index <= t else None
        fail = self.failed_index if self.failed_index is not None and self.failed_index <= t else None
        cdir = self.confirmed_direction if conf is not None else None
        return replace(self, confirmed_index=conf, confirmed_direction=cdir, failed_index=fail)

    def key(self) -> tuple[Any, ...]:
        return (
            self.detector,
            self.start_index,
            self.candidate_known_index,
            repr(sorted(self.geometry.items())),
        )


def _const(level: float) -> Callable[[int], float | None]:
    def boundary(_j: int) -> float | None:
        return level

    return boundary


def _line(line: Line) -> Callable[[int], float | None]:
    def boundary(j: int) -> float | None:
        return line.at(j)

    return boundary


def _resolve(
    bars: Bars,
    e: int,
    *,
    up: Callable[[int], float | None] | None = None,
    down: Callable[[int], float | None] | None = None,
    fail_up: float | None = None,
    fail_down: float | None = None,
) -> dict[str, Any]:
    """Search bars after ``e`` for the first breakout or failure. Empty dict if still open.

    A close beyond ``up``/``down`` confirms; a close beyond ``fail_up``/``fail_down``
    before any confirmation fails the candidate. Only future bars are read.
    """
    for j in range(e + 1, len(bars)):
        c = bars.close[j]
        upper = up(j) if up else None
        lower = down(j) if down else None
        if upper is not None and c > upper:
            return {"confirmed_index": j, "confirmed_direction": "bullish"}
        if lower is not None and c < lower:
            return {"confirmed_index": j, "confirmed_direction": "bearish"}
        if fail_down is not None and c < fail_down:
            return {"failed_index": j}
        if fail_up is not None and c > fail_up:
            return {"failed_index": j}
    return {}


# ---------------------------------------------------------------- range
def rectangle(
    bars: Bars, *, window: int = 20, max_range_frac: float = 0.15, tol_atr: float = 0.25, min_touches: int = 2
) -> list[Formation]:
    out: list[Formation] = []
    n = len(bars)
    for e in range(window - 1, n):
        a = atr(bars, e)
        if a is None:
            continue
        w = range(e - window + 1, e + 1)
        hi = max(bars.high[k] for k in w)
        lo = min(bars.low[k] for k in w)
        if (hi - lo) / bars.close[e] > max_range_frac:
            continue
        if not (lo <= bars.close[e] <= hi):
            continue
        tol = tol_atr * a
        if sum(bars.high[k] >= hi - tol for k in w) < min_touches:
            continue
        if sum(bars.low[k] <= lo + tol for k in w) < min_touches:
            continue
        if out and abs(float(out[-1].geometry["top"]) - hi) <= tol and e - out[-1].candidate_known_index < window:
            continue  # same consolidation already reported
        res = _resolve(bars, e, up=_const(hi), down=_const(lo))
        out.append(
            Formation(
                "rectangle", VERSION, e - window + 1, e,
                confirmed_index=res.get("confirmed_index"),
                confirmed_direction=res.get("confirmed_direction"),
                failed_index=res.get("failed_index"),
                geometry={"top": hi, "bottom": lo, "window": window},
            )
        )
    return out


# ---------------------------------------------------------------- triangles and wedges
def triangles(
    bars: Bars, *, window: int = 40, left: int = 2, right: int = 2, flat_tol: float = 0.0005
) -> list[Formation]:
    """Ascending, descending, symmetric triangles and rising/falling wedges from known pivots."""
    out: list[Formation] = []
    seen: set[tuple[Any, ...]] = set()
    n = len(bars)
    for e in range(window, n):
        pre = bars.head(e + 1)
        pivots = [p for p in causal_pivots(pre.high, pre.low, left=left, right=right) if p.index >= e - window + 1]
        highs = [p for p in pivots if p.kind == "high"]
        lows = [p for p in pivots if p.kind == "low"]
        if len(highs) < 2 or len(lows) < 2:
            continue
        h0, h1, l0, l1 = highs[0], highs[-1], lows[0], lows[-1]
        mean = sum(pre.close[e - window + 1 : e + 1]) / window
        line_hi = Line.through(h0.index, h0.price, h1.index, h1.price)
        line_lo = Line.through(l0.index, l0.price, l1.index, l1.price)
        s_hi, s_lo = line_hi.slope / mean, line_lo.slope / mean
        converging = (line_hi.at(e) - line_lo.at(e)) < (line_hi.at(e - window + 1) - line_lo.at(e - window + 1))
        if not converging:
            continue
        kind: str | None = None
        if abs(s_hi) <= flat_tol and s_lo > flat_tol:
            kind = "triangle_ascending"
        elif abs(s_lo) <= flat_tol and s_hi < -flat_tol:
            kind = "triangle_descending"
        elif s_hi < -flat_tol and s_lo > flat_tol:
            kind = "triangle_symmetric"
        elif s_hi > flat_tol and s_lo > flat_tol and s_lo > s_hi:
            kind = "wedge_rising"
        elif s_hi < -flat_tol and s_lo < -flat_tol and s_lo < s_hi:
            kind = "wedge_falling"
        if kind is None:
            continue
        used = [h0, h1, l0, l1]
        known = max(p.known_at_index for p in used)
        if known != e:
            continue  # emit only on the bar that makes the formation knowable (causal)
        key = (kind, h0.index, h1.index, l0.index, l1.index)
        if key in seen:
            continue
        seen.add(key)
        res = _resolve(
            bars, known,
            up=_line(line_hi),
            down=_line(line_lo),
        )
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


# ---------------------------------------------------------------- flags
def flags(
    bars: Bars, *, pole_len: int = 8, pole_min_ret: float = 0.08, flag_min: int = 5, flag_max: int = 15,
    max_retrace: float = 0.5,
) -> list[Formation]:
    out: list[Formation] = []
    seen: set[tuple[str, int]] = set()
    n = len(bars)
    for e in range(pole_len + flag_min, n):
        for flag_len in range(flag_min, flag_max + 1):
            p = e - flag_len
            start = p - pole_len
            if start < 0:
                break
            ret = (bars.close[p] - bars.close[start]) / bars.close[start]
            kind = None
            if ret >= pole_min_ret:
                kind = "flag_bullish"
            elif ret <= -pole_min_ret:
                kind = "flag_bearish"
            if kind is None:
                continue
            pole_hi = max(bars.high[start : p + 1])
            pole_lo = min(bars.low[start : p + 1])
            fl_hi = max(bars.high[p + 1 : e + 1])
            fl_lo = min(bars.low[p + 1 : e + 1])
            if kind == "flag_bullish":
                height = pole_hi - bars.low[start]
                retrace = (pole_hi - fl_lo) / height if height > 0 else None
                if retrace is None or not 0 <= retrace <= max_retrace or bars.close[e] > bars.close[p]:
                    continue
                if (kind, p) in seen:
                    break
                seen.add((kind, p))
                res = _resolve(bars, e, up=_const(fl_hi), fail_down=fl_lo)
            else:
                height = bars.high[start] - pole_lo
                retrace = (fl_hi - pole_lo) / height if height > 0 else None
                if retrace is None or not 0 <= retrace <= max_retrace or bars.close[e] < bars.close[p]:
                    continue
                if (kind, p) in seen:
                    break
                seen.add((kind, p))
                res = _resolve(bars, e, down=_const(fl_lo), fail_up=fl_hi)
            out.append(
                Formation(
                    kind, VERSION, start, e,
                    confirmed_index=res.get("confirmed_index"),
                    confirmed_direction=res.get("confirmed_direction"),
                    failed_index=res.get("failed_index"),
                    geometry={"pole_start": start, "pole_end": p, "flag_end": e, "retrace": round(retrace, 6)},
                )
            )
            break  # smallest valid flag length for this candidate end
    return out


# ---------------------------------------------------------------- double extrema
def double_extrema(
    bars: Bars, *, window: int = 60, left: int = 2, right: int = 2, min_sep: int = 5, tol_atr: float = 0.5
) -> list[Formation]:
    out: list[Formation] = []
    seen: set[tuple[Any, ...]] = set()
    for e in range(window, len(bars)):
        pre = bars.head(e + 1)
        a = atr(pre, e)
        if a is None:
            continue
        pivots = [p for p in causal_pivots(pre.high, pre.low, left=left, right=right) if p.index >= e - window]
        for kind in ("low", "high"):
            pts = [p for p in pivots if p.kind == kind]
            for p1, p2 in zip(pts, pts[1:], strict=False):
                if p2.index - p1.index < min_sep or abs(p1.price - p2.price) > tol_atr * a:
                    continue
                known = max(p1.known_at_index, p2.known_at_index)
                if known != e:
                    continue  # emit only on the bar that makes the formation knowable (causal)
                key = (kind, p1.index, p2.index)
                if key in seen:
                    continue
                seen.add(key)
                span = range(p1.index, p2.index + 1)
                if kind == "low":
                    neck = max(bars.high[k] for k in span)
                    res = _resolve(bars, known, up=_const(neck), fail_down=min(p1.price, p2.price))
                    name = "double_bottom"
                else:
                    neck = min(bars.low[k] for k in span)
                    res = _resolve(bars, known, down=_const(neck), fail_up=max(p1.price, p2.price))
                    name = "double_top"
                out.append(
                    Formation(
                        name, VERSION, p1.index, known,
                        confirmed_index=res.get("confirmed_index"),
                        confirmed_direction=res.get("confirmed_direction"),
                        failed_index=res.get("failed_index"),
                        geometry={"extrema": [p1.index, p2.index], "neckline": neck},
                    )
                )
    return out


# ---------------------------------------------------------------- head and shoulders
def head_shoulders(
    bars: Bars, *, window: int = 80, left: int = 2, right: int = 2, tol_atr: float = 0.75, trend_lag: int = 10
) -> list[Formation]:
    out: list[Formation] = []
    seen: set[tuple[Any, ...]] = set()
    for e in range(window, len(bars)):
        pre = bars.head(e + 1)
        a = atr(pre, e)
        if a is None:
            continue
        pivots = [p for p in causal_pivots(pre.high, pre.low, left=left, right=right) if p.index >= e - window]
        for kind in ("high", "low"):
            pts = [p for p in pivots if p.kind == kind]
            opp = [p for p in pivots if p.kind != kind]
            for h1, hd, h3 in zip(pts, pts[1:], pts[2:], strict=False):
                if kind == "high":
                    if not (hd.price > h1.price and hd.price > h3.price):
                        continue
                else:
                    if not (hd.price < h1.price and hd.price < h3.price):
                        continue
                if abs(h1.price - h3.price) > tol_atr * a:
                    continue
                if kind == "low":
                    # bottoms need a prior downtrend measured on bars already known
                    if h1.index < trend_lag or not bars.close[h1.index - trend_lag] > bars.close[h1.index]:
                        continue
                if h3.known_at_index != e:
                    continue  # emit only on the bar that makes the formation knowable (causal)
                key = (kind, h1.index, hd.index, h3.index)
                if key in seen:
                    continue
                seen.add(key)
                between1 = [p for p in opp if h1.index < p.index < hd.index]
                between2 = [p for p in opp if hd.index < p.index < h3.index]
                if not between1 or not between2:
                    continue
                s1 = min(between1, key=lambda p: p.price) if kind == "high" else max(between1, key=lambda p: p.price)
                s2 = min(between2, key=lambda p: p.price) if kind == "high" else max(between2, key=lambda p: p.price)
                neck = (s1.price + s2.price) / 2
                known = h3.known_at_index
                if kind == "high":
                    res = _resolve(bars, known, down=_const(neck), fail_up=hd.price)
                    name = "head_shoulders_top"
                else:
                    res = _resolve(bars, known, up=_const(neck), fail_down=hd.price)
                    name = "head_shoulders_bottom"
                out.append(
                    Formation(
                        name, VERSION, h1.index, known,
                        confirmed_index=res.get("confirmed_index"),
                        confirmed_direction=res.get("confirmed_direction"),
                        failed_index=res.get("failed_index"),
                        geometry={"shoulders": [h1.index, h3.index], "head": hd.index, "neckline": neck},
                    )
                )
    return out


DETECTORS: tuple[Callable[[Bars], list[Formation]], ...] = (
    rectangle,
    triangles,
    flags,
    double_extrema,
    head_shoulders,
)


def discover(bars: Bars) -> list[Formation]:
    """Batch discovery across every detector, in canonical order (CP-0090, CP-0069)."""
    found = [f for detector in DETECTORS for f in detector(bars)]
    return sorted(found, key=lambda f: (f.candidate_known_index, f.detector, f.start_index, str(f.geometry)))
