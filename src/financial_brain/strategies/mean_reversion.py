"""Mean-reversion and VWAP challengers, regime abstention, pairs spread and martingale guard (ALG-064..073).

These are negative controls, not recommendations. Every feature uses bars that are completed by the decision time,
and every function returns the evidence needed to show that a reversion idea lost money after costs as easily as
it made money.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from financial_brain.strategies.specs import decision_inputs_ok


def rolling_zscore(closes: Sequence[float], t: int, n: int = 20) -> float | None:
    """z-score of bar ``t`` against the mean and standard deviation of the ``n`` bars before it (ALG-064)."""
    if n < 2 or t < n or t >= len(closes):
        return None
    window = closes[t - n : t]
    if decision_inputs_ok(list(window) + [closes[t]]) != "OK":
        return None
    mean = sum(window) / n
    var = sum((x - mean) ** 2 for x in window) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return (closes[t] - mean) / sd


@dataclass(frozen=True)
class VwapBar:
    session: str
    high: float
    low: float
    close: float
    volume: float


def session_vwap(bars: Sequence[VwapBar]) -> list[float | None]:
    """VWAP that resets at every session boundary; prior sessions never contribute volume (ALG-065)."""
    out: list[float | None] = []
    current: str | None = None
    pv = 0.0
    vol = 0.0
    for b in bars:
        if b.session != current:
            current, pv, vol = b.session, 0.0, 0.0
        typical = (b.high + b.low + b.close) / 3
        if decision_inputs_ok([typical, b.volume if b.volume > 0 else None]) != "OK":
            out.append(None)
            continue
        pv += typical * b.volume
        vol += b.volume
        out.append(pv / vol)
    return out


def atr_normalised_distance(close: float, vwap: float | None, atr: float | None) -> float | None:
    if vwap is None or atr is None or decision_inputs_ok([atr]) != "OK":
        return None
    return (close - vwap) / atr


def vwap_rejection(lows: Sequence[float], closes: Sequence[float], vwap: Sequence[float | None], t: int) -> bool:
    """A closed bar that dipped under VWAP and closed back above it (ALG-066). Decided at bar ``t``; entry is later."""
    if t < 1 or t >= len(closes):
        return False
    v = vwap[t]
    if v is None or vwap[t - 1] is None:
        return False
    prev = vwap[t - 1]
    if prev is None:
        return False
    return lows[t - 1] < prev and closes[t - 1] < prev and closes[t] > v


def regime_abstain(closes: Sequence[float], t: int, *, short: int = 10, long: int = 60, ratio: float = 1.8) -> bool:
    """Abstain when short-window volatility is far above long-window volatility, using lagged bars only (ALG-068)."""
    if t < long + 1 or t >= len(closes):
        return True
    rets = [closes[k] / closes[k - 1] - 1 for k in range(1, t)]  # returns strictly before t
    if len(rets) < long:
        return True
    def sd(x: list[float]) -> float:
        m = sum(x) / len(x)
        return math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))
    s_short = sd(rets[-short:])
    s_long = sd(rets[-long:])
    if s_long == 0:
        return True
    return s_short / s_long > ratio


def pair_spread_z(train_a: Sequence[float], train_b: Sequence[float], test_a: Sequence[float], test_b: Sequence[float]) -> list[float]:
    """Hedge ratio fit on the training sample only; z-scores of the spread on the untouched test sample (ALG-069)."""
    if len(train_a) != len(train_b) or len(test_a) != len(test_b) or len(train_a) < 3:
        raise ValueError("samples must be equal length and at least three bars")
    beta = sum(a * b for a, b in zip(train_a, train_b, strict=True)) / sum(b * b for b in train_b)
    spread_train = [a - beta * b for a, b in zip(train_a, train_b, strict=True)]
    m = sum(spread_train) / len(spread_train)
    sd = math.sqrt(sum((s - m) ** 2 for s in spread_train) / (len(spread_train) - 1))
    if sd == 0:
        raise ValueError("training spread has no variance")
    return [((a - beta * b) - m) / sd for a, b in zip(test_a, test_b, strict=True)]


def pair_is_tradable(short_leg_segment: str) -> bool:
    """Cash equity cannot be shorted in these specs, so a pair with a cash short leg is rejected (ALG-070)."""
    return short_leg_segment != "CASH_EQUITY"


def next_size_after_loss(base_units: int, last_pnl: float) -> int:
    """Martingale and averaging-down are disabled: size never grows after a loss (ALG-071)."""
    if base_units < 0:
        raise ValueError("base units must be non-negative")
    return base_units


def family_overlap_note(family: str) -> list[str]:
    """Prior null evidence stays visible in the family report (ALG-072)."""
    notes = {
        "mean_reversion": ["K7 candle and RSI-like research rejected 17 of 17 setups; treat reversion challengers as the same null family."],
    }
    return notes.get(family, [])


def after_cost_utility(gross_returns: Sequence[float], turnover: Sequence[float], cost_bps: float) -> dict[str, float]:
    """Net return after proportional costs, with turnover shown next to it (ALG-073)."""
    if len(gross_returns) != len(turnover):
        raise ValueError("series must be the same length")
    cost = cost_bps / 10_000
    net = [g - t * cost for g, t in zip(gross_returns, turnover, strict=True)]
    return {"gross_total": sum(gross_returns), "net_total": sum(net), "turnover_total": sum(turnover)}
