"""15-minute index bars, labels, point-in-time features and leakage controls (checklist 24A/24B/24C/24F).

* ``bars_15m`` aggregates 1-minute index prices into 15-minute OHLC bars aligned to the session
  open. A bucket missing any minute is marked partial and never presented as a full bar. Index
  packets carry no volume, so none is required.
* ``label`` defines the forward return from a *completed* bar's close to the close of the bar
  ``h`` steps later, and a three-class UP/DOWN/FLAT label from a versioned noise threshold.
* ``features_at`` reads only bars whose end is at or before the decision instant.
* ``leak_check`` mutates every bar after the cutoff and asserts the features do not change.
* ``purged_splits`` builds chronological train/test folds with an embargo wider than the label.
* ``frequency_baseline`` is the naive class-frequency forecaster every model must beat.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

STEP = timedelta(minutes=15)


class SeriesError(ValueError):
    pass


@dataclass(frozen=True)
class Bar:
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    complete: bool


def bars_15m(minutes: list[tuple[datetime, float]], session_open: datetime,
             session_close: datetime) -> list[Bar]:
    """Aggregate (minute start, price) points into session-aligned 15-minute bars."""
    if session_open.tzinfo is None:
        raise SeriesError("session times must be timezone-aware")
    by_bucket: dict[int, list[tuple[datetime, float]]] = {}
    for ts, px in sorted(minutes):
        if ts < session_open or ts >= session_close:
            continue
        idx = int((ts - session_open) / STEP)
        by_bucket.setdefault(idx, []).append((ts, px))
    out = []
    for idx in sorted(by_bucket):
        pts = by_bucket[idx]
        start = session_open + idx * STEP
        end = min(start + STEP, session_close)
        expected = int((end - start) / timedelta(minutes=1))
        prices = [p for _, p in pts]
        out.append(Bar(start, end, prices[0], max(prices), min(prices), prices[-1],
                       complete=len({t for t, _ in pts}) == expected))
    return out


def completed_before(bars: list[Bar], decision_at: datetime) -> list[Bar]:
    """Bars a strategy could know at ``decision_at``: complete and ended no later than it."""
    return [b for b in bars if b.complete and b.end <= decision_at]


def forward_return(bars: list[Bar], i: int, steps: int = 1) -> float | None:
    """r(i, t, h) = close(t + h) / close(t) - 1, or None when the future bar is missing/partial."""
    j = i + steps
    if j >= len(bars) or not bars[i].complete or not bars[j].complete:
        return None
    if bars[j].start - bars[i].start != steps * STEP:
        return None
    return bars[j].close / bars[i].close - 1


def label(ret: float | None, threshold: float) -> str | None:
    if ret is None:
        return None
    if threshold <= 0:
        raise SeriesError("threshold must be positive")
    return "UP" if ret > threshold else "DOWN" if ret < -threshold else "FLAT"


def noise_threshold(train_returns: list[float], quantile: float = 0.33) -> float:
    """Index-specific FLAT band from training-period absolute returns only."""
    if not train_returns:
        raise SeriesError("no training returns")
    s = sorted(abs(r) for r in train_returns)
    return s[min(len(s) - 1, int(quantile * len(s)))]


def features_at(bars: list[Bar], decision_at: datetime, session_open: datetime) -> dict[str, float]:
    """Point-in-time features from completed bars only."""
    known = completed_before(bars, decision_at)
    if len(known) < 2:
        raise SeriesError("not enough completed bars")
    last, prev = known[-1], known[-2]
    rets = [b.close / a.close - 1 for a, b in zip(known, known[1:], strict=False)]
    mean = sum(rets) / len(rets)
    vol = (sum((r - mean) ** 2 for r in rets) / len(rets)) ** 0.5
    first = known[0]
    return {
        "ret_15m": last.close / prev.close - 1,
        "ret_session": last.close / first.open - 1,
        "range_last": (last.high - last.low) / last.close,
        "vol_session": vol,
        "minutes_since_open": (decision_at - session_open).total_seconds() / 60,
        "opening_range_pos": (last.close - first.low) / ((first.high - first.low) or 1.0),
    }


def leak_check(bars: list[Bar], decision_at: datetime, session_open: datetime) -> bool:
    """True when mutating every bar not yet completed at ``decision_at`` leaves features unchanged."""
    base = features_at(bars, decision_at, session_open)
    shocked = [b if (b.complete and b.end <= decision_at) else
               replace(b, open=b.open * 3, high=b.high * 3, low=b.low * 0.1, close=b.close * 5)
               for b in bars]
    return features_at(shocked, decision_at, session_open) == base


def purged_splits(n: int, folds: int, label_steps: int, embargo: int = 1
                  ) -> list[tuple[list[int], list[int]]]:
    """Chronological folds; training never includes indices within label + embargo of the test."""
    if folds < 2 or n < folds:
        raise SeriesError("need at least two folds and one row per fold")
    size = n // folds
    out = []
    for k in range(1, folds):
        test = list(range(k * size, min(n, (k + 1) * size)))
        gap = label_steps + embargo
        train = [i for i in range(0, test[0]) if i + gap < test[0]]
        out.append((train, test))
    return out


def frequency_baseline(train_labels: list[str]) -> Callable[[], dict[str, float]]:
    """Class-frequency forecaster; probabilities sum to 1."""
    c = Counter(lbl for lbl in train_labels if lbl in ("UP", "DOWN", "FLAT"))
    total = sum(c.values())
    if not total:
        raise SeriesError("no labels")
    probs = {k: c.get(k, 0) / total for k in ("UP", "DOWN", "FLAT")}
    return lambda: dict(probs)


def check_probabilities(p: dict[str, float], tol: float = 1e-9) -> None:
    if set(p) != {"UP", "DOWN", "FLAT"} or any(v < 0 for v in p.values()):
        raise SeriesError("probabilities must cover UP, DOWN, FLAT and be non-negative")
    if abs(sum(p.values()) - 1) > tol:
        raise SeriesError("probabilities must sum to 1")


def brier(p: dict[str, float], actual: str) -> float:
    check_probabilities(p)
    return sum((p[k] - (1.0 if k == actual else 0.0)) ** 2 for k in p)
