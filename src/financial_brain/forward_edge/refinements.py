"""Offline refinements of earlier partial items (F15). Pure computation; synthetic inputs only in tests.

FE-0059 inventory across slices, FE-0062 legging loss, FE-0066 fill counterfactuals, FE-0067 settlement and financing
identity, FE-0093 two-way clustering, FE-0094 Reality Check (max-t bootstrap), FE-0095 drawdown uncertainty and turnover,
FE-0101 stopping policy inside the verdict, FE-0106 combined allocator, FE-0076 immutable completeness file.
"""
from __future__ import annotations

import json
from decimal import Decimal
from math import sqrt
from pathlib import Path

import numpy as np


class RefinementError(ValueError):
    pass


# ---- FE-0059 inventory across freeze-sized slices ----

def slice_inventory(quantity: int, freeze_qty: int, fills_per_slice: list[int]) -> dict:
    """Each slice is its own order; inventory is the sum of what each slice actually filled."""
    from financial_brain.forward_edge.execution import slice_for_freeze
    slices = slice_for_freeze(quantity, freeze_qty)
    if len(fills_per_slice) != len(slices):
        raise RefinementError("one fill count per slice required")
    if any(f < 0 or f > s for f, s in zip(fills_per_slice, slices)):
        raise RefinementError("a slice cannot fill more than its size")
    filled = sum(fills_per_slice)
    return {"slices": slices, "filled": filled, "unfilled": quantity - filled, "complete": filled == quantity}


# ---- FE-0062 legging loss ----

def legging_loss(*, first_leg_fill: Decimal, second_leg_planned: Decimal, second_leg_actual: Decimal, units: int,
                 first_is_buy: bool) -> Decimal:
    """Loss from the second leg of a spread filling away from plan after the first has filled. Positive = loss."""
    adverse = (second_leg_actual - second_leg_planned) if not first_is_buy else (second_leg_planned - second_leg_actual)
    return max(Decimal(0), adverse * units)


# ---- FE-0066 counterfactual fills with bounds ----

def counterfactual_fills(prices: list[Decimal], side: str) -> dict:
    """Fill prices the same intent would have received at each plausible arrival time, with summary bounds."""
    if not prices:
        raise RefinementError("no candidate fills")
    s = sorted(prices)
    best, worst = (s[0], s[-1]) if side == "BUY" else (s[-1], s[0])
    mean = sum(s, Decimal(0)) / len(s)
    return {"n": len(s), "best": best, "worst": worst, "mean": mean, "range": worst - best if side == "BUY" else best - worst}


# ---- FE-0067 settlement, financing and margin identity ----

def settlement_identity(*, cash_before: Decimal, cash_after: Decimal, trade_value: Decimal, fees: Decimal,
                        financing: Decimal, margin_blocked_before: Decimal, margin_blocked_after: Decimal,
                        margin_released: Decimal, side: str) -> bool:
    """Cash moves by the trade (sign by side) less fees and financing; blocked margin moves only by what was released."""
    sign = Decimal(-1) if side == "BUY" else Decimal(1)
    cash_ok = (cash_after - cash_before) == sign * trade_value - fees - financing
    margin_ok = (margin_blocked_before - margin_blocked_after) == margin_released
    return cash_ok and margin_ok


# ---- FE-0093 two-way clustered standard error of a mean ----

def two_way_cluster_se(values: list[float], dates: list[str], names: list[str]) -> float:
    """Cameron-Gelbach-Miller: V = V_date + V_name - V_(date,name) for the sample mean."""
    n = len(values)
    if not (n == len(dates) == len(names)) or n < 4:
        raise RefinementError("aligned values of length >= 4 required")
    mean = sum(values) / n

    def one_way(keys: list[str]) -> float:
        sums: dict[str, float] = {}
        for v, k in zip(values, keys):
            sums[k] = sums.get(k, 0.0) + (v - mean)
        g = len(sums)
        if g < 2:
            return 0.0
        return g / (g - 1) * sum(x * x for x in sums.values()) / (n * n)

    v = one_way(dates) + one_way(names) - one_way([f"{d}|{m}" for d, m in zip(dates, names)])
    return sqrt(max(v, 0.0))


# ---- FE-0094 White's Reality Check (stationary bootstrap of the max mean) ----

def reality_check_pvalue(excess_returns: dict[str, list[float]], *, resamples: int = 1000, mean_block: float = 5.0,
                         seed: int = 0) -> dict:
    """p-value that the BEST of all tested candidates beats the benchmark by chance. Every candidate counts."""
    names = sorted(excess_returns)
    mat = np.array([excess_returns[k] for k in names], dtype=float)            # candidates x time
    m, n = mat.shape
    if m < 1 or n < 5:
        raise RefinementError("need at least one candidate and five observations")
    means = mat.mean(axis=1)
    observed = float(np.sqrt(n) * means.max())
    centred = mat - means[:, None]
    rng = np.random.default_rng(seed)
    p = 1.0 / mean_block
    exceed = 0
    for _ in range(resamples):
        idx = np.empty(n, dtype=int)
        idx[0] = rng.integers(n)
        for t in range(1, n):
            idx[t] = rng.integers(n) if rng.random() < p else (idx[t - 1] + 1) % n
        stat = float(np.sqrt(n) * centred[:, idx].mean(axis=1).max())
        exceed += stat >= observed
    return {"best": names[int(np.argmax(means))], "statistic": observed, "p_value": (exceed + 1) / (resamples + 1),
            "candidates_counted": m}


# ---- FE-0095 drawdown uncertainty, tail drawdown, turnover ----

def drawdown_ci(returns: list[float], *, resamples: int = 500, mean_block: float = 5.0, seed: int = 0) -> tuple[float, float]:
    a = np.asarray(returns, dtype=float)
    n = len(a)
    if n < 10:
        raise RefinementError("too few observations")
    rng = np.random.default_rng(seed)
    p = 1.0 / mean_block
    dds = np.empty(resamples)
    for b in range(resamples):
        idx = np.empty(n, dtype=int)
        idx[0] = rng.integers(n)
        for t in range(1, n):
            idx[t] = rng.integers(n) if rng.random() < p else (idx[t - 1] + 1) % n
        eq = np.cumprod(1.0 + a[idx])
        dds[b] = (eq / np.maximum.accumulate(eq) - 1.0).min()
    return float(np.quantile(dds, 0.05)), float(np.quantile(dds, 0.95))


def tail_conditional_drawdown(returns: list[float], alpha: float = 0.1) -> float:
    eq = np.cumprod(1.0 + np.asarray(returns, dtype=float))
    dd = eq / np.maximum.accumulate(eq) - 1.0
    k = max(1, int(len(dd) * alpha))
    return float(np.sort(dd)[:k].mean())


def turnover(weights_by_day: list[dict[str, float]]) -> float:
    """Mean one-way turnover: half the sum of absolute weight changes per rebalance."""
    if len(weights_by_day) < 2:
        raise RefinementError("need at least two days")
    total = 0.0
    for a, b in zip(weights_by_day, weights_by_day[1:]):
        total += 0.5 * sum(abs(b.get(k, 0.0) - a.get(k, 0.0)) for k in set(a) | set(b))
    return total / (len(weights_by_day) - 1)


# ---- FE-0101 stopping policy consulted by the verdict ----

def verdict_at_checkpoint(sessions: int, checkpoints: tuple[int, ...], verdict: dict) -> dict:
    """Between frozen checkpoints the verdict is withheld: peeking cannot speed promotion."""
    if sessions not in checkpoints:
        return {"verdict": "NOT_EVALUATED_BETWEEN_CHECKPOINTS", "next_checkpoint": min((c for c in checkpoints if c > sessions), default=None)}
    return verdict


# ---- FE-0106 deterministic combined allocator ----

def allocate(raw_scores: dict[str, float], vols: dict[str, float], *, target_vol: float, max_weight: float,
             cash_floor: float = 0.0) -> dict[str, float]:
    """Inverse-vol weights scaled to a vol target, each name capped, the rest in CASH. Deterministic, bounded, explainable."""
    if not raw_scores or set(raw_scores) != set(vols) or any(v <= 0 for v in vols.values()):
        raise RefinementError("scores and positive vols must cover the same names")
    longs = {k: s for k, s in raw_scores.items() if s > 0}
    if not longs:
        return {"CASH": 1.0}
    inv = {k: 1.0 / vols[k] for k in longs}
    total = sum(inv.values())
    base = {k: v / total for k, v in inv.items()}
    port_vol = sqrt(sum((base[k] * vols[k]) ** 2 for k in base))      # zero-correlation approximation, stated, not hidden
    scale = min(1.0, target_vol / port_vol) if port_vol > 0 else 1.0
    w = {k: min(max_weight, base[k] * scale) for k in sorted(base)}
    invested = min(sum(w.values()), 1.0 - cash_floor)
    if sum(w.values()) > invested:
        f = invested / sum(w.values())
        w = {k: v * f for k, v in w.items()}
    w["CASH"] = max(0.0, 1.0 - sum(w.values()))
    return w


# ---- FE-0076 immutable completeness report ----

def write_completeness_once(report: dict, path: Path) -> None:
    if path.exists():
        raise RefinementError("completeness report already written: it is immutable")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(report, sort_keys=True, indent=1) + "\n")
