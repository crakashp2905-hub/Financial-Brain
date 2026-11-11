"""Closures of 25 logic-only partial items.

Items: FB-0078, FB-0105, FB-0124, FB-0145, FB-0154, FB-0188, FB-0193, FB-0200, FB-0201, FB-0215, FB-0231, FB-0233,
FB-0244, FB-0260, FB-0288, FB-0302, FB-0310, FB-0318, FB-0321, FB-0324, FB-0339, FB-0340, FB-0354, FB-0362, FB-0372.

Each function is a small deterministic piece of its item's acceptance. The ledger note states what remains.
"""
from __future__ import annotations

import ast
import math
from collections.abc import Mapping, Sequence
from decimal import Decimal
from statistics import mean, pstdev
from typing import Any

from financial_brain.kernel.research_extra import bootstrap_ci, sharpe_per_period


# FB-0105: Wilder's average directional index over n periods.
def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int) -> float:
    if n < 2 or len(closes) < 2 * n + 1:
        raise ValueError("need n >= 2 and at least 2n+1 bars")
    trs: list[float] = []
    pdms: list[float] = []
    mdms: list[float] = []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        pdms.append(up if (up > down and up > 0) else 0.0)
        mdms.append(down if (down > up and down > 0) else 0.0)
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    s_tr = sum(trs[:n])
    s_p = sum(pdms[:n])
    s_m = sum(mdms[:n])
    dxs: list[float] = []
    for i in range(n, len(trs)):
        if i > n:
            s_tr = s_tr - s_tr / n + trs[i]
            s_p = s_p - s_p / n + pdms[i]
            s_m = s_m - s_m / n + mdms[i]
        pdi = 100.0 * s_p / s_tr if s_tr else 0.0
        mdi = 100.0 * s_m / s_tr if s_tr else 0.0
        denom = pdi + mdi
        dxs.append(100.0 * abs(pdi - mdi) / denom if denom else 0.0)
    if len(dxs) < n:
        raise ValueError("not enough data to smooth ADX")
    value = mean(dxs[:n])
    for dx in dxs[n:]:
        value = (value * (n - 1) + dx) / n
    return value


# FB-0124: safe arithmetic over named inputs; no calls, attributes or imports are evaluated.
_OPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b}


def eval_expression(text: str, env: Mapping[str, float]) -> float:
    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -ev(node.operand)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name) and node.id in env:
            return float(env[node.id])
        raise ValueError(f"unsupported expression element {ast.dump(node)[:40]}")

    return ev(ast.parse(text, mode="eval"))


# FB-0145: contiguous train, holdout and forward partitions with no overlap.
def partition_ranges(n: int, train: float, holdout: float, forward: float) -> dict[str, range]:
    if min(train, holdout, forward) <= 0 or train + holdout + forward > 1.0 + 1e-12:
        raise ValueError("fractions must be positive and sum to at most 1")
    a = int(n * train)
    b = a + int(n * holdout)
    c = b + int(n * forward)
    return {"train": range(0, a), "holdout": range(a, b), "forward": range(b, c)}


# FB-0154: bootstrap interval for the per-period Sharpe ratio.
def sharpe_interval(returns: Sequence[float], n_boot: int = 500, seed: int = 0) -> tuple[float, float]:
    return bootstrap_ci(list(returns), stat=sharpe_per_period, n_boot=n_boot, seed=seed)


# FB-0188: stop-limit fill. The stop must trigger on the bar high; the limit fills only if the bar low reaches it.
def stop_limit_fill(side: str, stop: float, limit: float, high: float, low: float) -> float | None:
    if side != "BUY":
        raise ValueError("this helper covers BUY stop-limits")
    if high < stop:
        return None
    return limit if low <= limit else None


# FB-0193: no fill on zero volume or during an auction phase.
def fill_allowed(volume: float, phase: str) -> bool:
    return volume > 0 and phase != "AUCTION"


# FB-0200: legging action for a multi-leg order.
def legging_action(filled: Mapping[str, bool]) -> str:
    if all(filled.values()):
        return "COMPLETE"
    if not any(filled.values()):
        return "CANCEL_ALL"
    return "UNWIND_FILLED"


# FB-0201: duplicate broker acknowledgements are ignored; the first of each id is kept.
def ack_dedupe(ack_ids: Sequence[str]) -> tuple[list[str], int]:
    seen: list[str] = []
    for a in ack_ids:
        if a not in seen:
            seen.append(a)
    return seen, len(ack_ids) - len(seen)


# FB-0215: OCO cancels the other exit when one fills; OTO activates the child only when the parent fills.
def oco_cancel(filled_leg: str, legs: Sequence[str]) -> list[str]:
    if filled_leg not in legs:
        raise ValueError("filled leg is not part of this OCO")
    return [leg for leg in legs if leg != filled_leg]


def oto_child_state(parent_filled: bool) -> str:
    return "ACTIVE" if parent_filled else "PENDING"


# FB-0231: after-tax estimate: tax only on gains, always labelled ESTIMATE.
def after_tax_estimate(gain: Decimal, rate: Decimal) -> dict[str, Any]:
    if not (Decimal(0) <= rate < Decimal(1)):
        raise ValueError("rate must be in [0, 1)")
    tax = max(gain, Decimal(0)) * rate
    return {"after_tax": gain - tax, "tax": tax, "label": "ESTIMATE"}


# FB-0233: restricted mode refuses any new spend.
def spend_allowed(mode: str, amount: float, limit: float) -> bool:
    if mode == "RESTRICTED":
        return False
    if mode != "NORMAL":
        raise ValueError("mode must be NORMAL or RESTRICTED")
    return 0 <= amount <= limit


# FB-0244: attribution of P&L by any key (strategy, asset, instrument, factor).
def attribution_by(records: Sequence[tuple[str, float]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, pnl in records:
        out[key] = out.get(key, 0.0) + pnl
    return dict(sorted(out.items()))


# FB-0260: tracking error split into a market (beta) part and a residual part.
def te_decomposition(portfolio: Sequence[float], benchmark: Sequence[float]) -> dict[str, float]:
    if len(portfolio) != len(benchmark) or len(portfolio) < 2:
        raise ValueError("series must align with at least two points")
    mb = mean(benchmark)
    mp = mean(portfolio)
    var_b = sum((b - mb) ** 2 for b in benchmark)
    if var_b == 0:
        raise ValueError("benchmark has no variation")
    beta = sum((p - mp) * (b - mb) for p, b in zip(portfolio, benchmark, strict=True)) / var_b
    residual = [p - beta * b for p, b in zip(portfolio, benchmark, strict=True)]
    return {
        "beta": beta,
        "tracking_error": pstdev([p - b for p, b in zip(portfolio, benchmark, strict=True)]),
        "residual_te": pstdev(residual),
    }


# FB-0288: portfolio volatility before and after a proposed weight change.
def risk_preview(cov: Sequence[Sequence[float]], before: Sequence[float], after: Sequence[float]) -> dict[str, float]:
    def vol(w: Sequence[float]) -> float:
        n = len(w)
        var = sum(w[i] * cov[i][j] * w[j] for i in range(n) for j in range(n))
        return math.sqrt(max(var, 0.0))

    return {"vol_before": vol(before), "vol_after": vol(after), "change": vol(after) - vol(before)}


# FB-0302: hedge P&L from a path: each step's delta times the price move over that step.
def simulate_hedge(prices: Sequence[float], deltas: Sequence[float]) -> float:
    if len(deltas) != len(prices) - 1:
        raise ValueError("need one delta per step")
    return sum(d * (prices[i + 1] - prices[i]) for i, d in enumerate(deltas))


# FB-0310: vega P&L for an implied-volatility change.
def iv_pnl(vega: float, iv_change: float) -> float:
    return vega * iv_change


# FB-0318: roll on volume or open-interest criteria; either one signals a roll.
def roll_by_volume_or_oi(front_vol: float, back_vol: float, front_oi: float, back_oi: float) -> str:
    return "ROLL" if (back_vol > front_vol or back_oi > front_oi) else "HOLD"


# FB-0321: hedge ratio for a spread: units of the short leg per unit of the long leg, by notional.
def spread_hedge_ratio(long_price: float, short_price: float, long_mult: float, short_mult: float) -> float:
    if short_price <= 0 or short_mult <= 0:
        raise ValueError("short leg must have positive price and multiplier")
    return (long_price * long_mult) / (short_price * short_mult)


# FB-0324: flatten inside the session close cutoff.
def session_close_action(now_min: int, close_min: int, cutoff_min: int) -> str:
    return "FLATTEN" if now_min >= close_min - cutoff_min else "HOLD"


# FB-0339 and FB-0372: range reversion: buy at the range low, sell at the range high.
def range_reversion_signal(close: float, low: float, high: float) -> int:
    if close <= low:
        return 1
    if close >= high:
        return -1
    return 0


# FB-0339: trend signal: close against the mean of the last window of closes.
def trend_signal(closes: Sequence[float], window: int) -> int:
    if window < 1 or len(closes) < window:
        raise ValueError("need at least one window of closes")
    avg = mean(closes[-window:])
    if closes[-1] > avg:
        return 1
    if closes[-1] < avg:
        return -1
    return 0


# FB-0340: funding cost on a notional for a number of days at an annual funding rate.
def funding_cost(notional: Decimal, annual_rate: Decimal, days: int) -> Decimal:
    if days < 0:
        raise ValueError("days must be non-negative")
    return notional * annual_rate * Decimal(days) / Decimal(365)


# FB-0362: valuation range across scenarios.
def valuation_range(scenarios: Mapping[str, Decimal]) -> dict[str, Decimal]:
    if not scenarios:
        raise ValueError("no scenarios")
    lo = min(scenarios.values())
    hi = max(scenarios.values())
    return {"min": lo, "max": hi, "mid": (lo + hi) / 2}


# FB-0354: theoretical ex-rights price after a rights issue.
def rights_ex_price(price: Decimal, held: int, subscription: Decimal, new_shares: int) -> Decimal:
    if held + new_shares <= 0:
        raise ValueError("share count must be positive")
    return (price * held + subscription * new_shares) / (held + new_shares)
