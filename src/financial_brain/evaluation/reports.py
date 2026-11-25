"""Daily reports, attribution, risk diagnostics, robustness and evidence bundles
(FB-0912, FB-0913, FB-0915, FB-1101, FB-1104, FB-1111, FB-1207, FB-1209, FB-1210, FB-1211, FB-1214,
FB-1216, FB-1601, FB-1706, FB-1804, FB-1805, FB-2101, FB-2102, FB-2105, FB-2107, FB-2201, FB-2202,
FB-2203, FB-2208, FB-2305).
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field


class ReportError(ValueError):
    pass


# ---------------------------------------------------------------- daily report and attribution

def daily_report(desk: str, *, opening_nav: float, flows: float, realized: float, unrealized: float,
                 fees: float, closed: int, open_: int, exposure: float) -> dict:
    """FB-2101/FB-1101: flows are not returns; net return is on opening NAV plus flows."""
    base = opening_nav + flows
    if base <= 0:
        raise ReportError("non-positive base")
    net = realized + unrealized - fees
    return {"desk": desk, "opening_nav": opening_nav, "flows": flows, "realized": realized,
            "unrealized": unrealized, "fees": fees, "net_pnl": net, "net_return": net / base,
            "closed": closed, "open": open_, "exposure": exposure}


def attribution(*, forecast_error: float, market: float, timing: float, spread: float, impact: float,
                fees: float, unpriced: float, total: float) -> dict:
    """FB-2102/FB-2105: deterministic decomposition; the residual is shown, never invented away."""
    parts = {"forecast_error": forecast_error, "market": market, "timing": timing, "spread": -spread,
             "impact": -impact, "fees": -fees, "unpriced": unpriced}
    parts["residual"] = total - sum(parts.values())
    top = sorted(parts.items(), key=lambda kv: kv[1])
    return {"parts": parts, "top_negative": top[:2], "top_positive": top[-2:][::-1]}


def attribute_by(trades: list[dict], key: str) -> dict[str, float]:
    """FB-0912: P&L by desk/strategy/version/sector/underlying; shared positions split by weight."""
    out: dict[str, float] = {}
    for t in trades:
        w = t.get("weight", 1.0)
        out[str(t[key])] = out.get(str(t[key]), 0.0) + t["pnl"] * w
    return out


# ---------------------------------------------------------------- risk diagnostics

def var_es(pnls: Sequence[float], alpha: float = 0.95, liquidity_haircut: float = 0.0) -> tuple[float, float]:
    """FB-0913: historical VaR/ES (positive = loss) with a liquidity add-on."""
    if not pnls:
        raise ReportError("no P&L history")
    s = sorted(pnls)
    k = max(1, int(len(s) * (1 - alpha)))
    var = -s[k - 1]
    es = -statistics.fmean(s[:k])
    return var * (1 + liquidity_haircut), es * (1 + liquidity_haircut)


def beta(asset: Sequence[float], market: Sequence[float]) -> float:
    mv = statistics.pvariance(market)
    if mv == 0:
        raise ReportError("flat market series")
    ma, mm = statistics.fmean(asset), statistics.fmean(market)
    return sum((a - ma) * (m - mm) for a, m in zip(asset, market, strict=True)) / len(asset) / mv


@dataclass(frozen=True)
class Limits:
    max_drawdown: float
    per_instrument: float
    per_underlying: float
    premium_at_risk: float


def limit_breaches(lim: Limits, *, drawdown: float, by_instrument: dict[str, float],
                   by_underlying: dict[str, float], premium: float) -> list[str]:
    """FB-0915."""
    b = []
    if drawdown > lim.max_drawdown:
        b.append("drawdown")
    b += [f"instrument {k}" for k, v in by_instrument.items() if v > lim.per_instrument]
    b += [f"underlying {k}" for k, v in by_underlying.items() if v > lim.per_underlying]
    if premium > lim.premium_at_risk:
        b.append("premium at risk")
    return b


def max_drawdown(navs: Sequence[float]) -> float:
    peak, mdd = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    return mdd


# ---------------------------------------------------------------- performance and robustness

def desk_stats(rets: Sequence[float], turnover: float, shortfall_bps: float) -> dict:
    """FB-1207 with a normal-approximation CI on the mean."""
    n = len(rets)
    if n < 2:
        raise ReportError("need at least two returns")
    m, sd = statistics.fmean(rets), statistics.stdev(rets)
    half = 1.96 * sd / math.sqrt(n)
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    return {"expectancy": m, "ci": (m - half, m + half), "hit_rate": len(wins) / n,
            "payoff": (statistics.fmean(wins) / -statistics.fmean(losses)) if wins and losses and statistics.fmean(losses) else None,
            "sharpe": m / sd * math.sqrt(250) if sd else None, "turnover": turnover,
            "shortfall_bps": shortfall_bps}


def cost_robustness(gross: Sequence[float], cost: float) -> dict[str, float]:
    """FB-1209: net expectancy at 1x, 1.5x and 2x costs."""
    return {f"{m}x": statistics.fmean(g - m * cost for g in gross) for m in (1.0, 1.5, 2.0)}


def effective_n(rets: Sequence[float], holding: int) -> float:
    """FB-1210: overlap and first-order autocorrelation shrink the independent sample count."""
    n = len(rets)
    if n < 3:
        return float(n)
    m = statistics.fmean(rets)
    den = sum((r - m) ** 2 for r in rets)
    rho = sum((rets[i] - m) * (rets[i - 1] - m) for i in range(1, n)) / den if den else 0.0
    rho = max(min(rho, 0.99), -0.99)
    return n / max(holding, 1) * (1 - rho) / (1 + rho)


def fill_coverage(intents: int, filled: int, unknown: int) -> dict[str, float]:
    """FB-1211: the denominator is intents; no-fill and unknown are coverage problems."""
    if intents <= 0:
        raise ReportError("no intents")
    return {"fill_rate": filled / intents, "unknown_rate": unknown / intents,
            "missed_rate": (intents - filled - unknown) / intents}


def desk_board(results: dict[str, float]) -> list[tuple[str, float]]:
    """FB-1214: worst desks first, same prominence as the best."""
    return sorted(results.items(), key=lambda kv: kv[1])


def committee(marginal: dict[str, float]) -> dict[str, float]:
    """FB-1216: a desk with non-positive marginal risk-adjusted net gets zero capital."""
    pos = {k: v for k, v in marginal.items() if v > 0}
    tot = sum(pos.values())
    return {k: (pos[k] / tot if k in pos and tot else 0.0) for k in marginal}


def no_streak_chasing(weights_prev: dict[str, float], weights_new: dict[str, float],
                      long_run_edge: dict[str, float]) -> list[str]:
    """FB-1706: refuse raising weight on a strategy whose long-run edge is not positive."""
    return [k for k, w in weights_new.items() if w > weights_prev.get(k, 0) and long_run_edge.get(k, 0) <= 0]


def ev_interval(outcomes: Sequence[float], draws: int = 2000, seed: int = 5) -> tuple[float, float]:
    """FB-1601: bootstrap 90% interval for expected value."""
    rng = random.Random(seed)
    n = len(outcomes)
    if n < 2:
        raise ReportError("need outcomes")
    means = sorted(statistics.fmean(outcomes[rng.randrange(n)] for _ in range(n)) for _ in range(draws))
    return means[int(0.05 * draws)], means[int(0.95 * draws)]


# ---------------------------------------------------------------- execution simulation

def depth_fill(levels: Sequence[tuple[float, int]], qty: int, participation: float) -> tuple[int, float | None]:
    """FB-1804: walk the book within a participation cap; returns filled qty and average price."""
    left, cost, got = qty, 0.0, 0
    for px, size in levels:
        take = min(left, int(size * participation))
        cost += take * px
        got += take
        left -= take
        if left == 0:
            break
    return got, (cost / got if got else None)


def shortfall_bps(decision_px: float, fill_px: float, side: str) -> float:
    """FB-1805: implementation shortfall vs decision price."""
    sign = 1 if side == "BUY" else -1
    return sign * (fill_px - decision_px) / decision_px * 1e4


# ---------------------------------------------------------------- counterfactual regret

def selection_regret(taken: Sequence[float], rejected: Sequence[float]) -> dict[str, float]:
    """FB-2107: mean rejected minus mean taken, never credited as P&L."""
    if not taken or not rejected:
        return {"regret": 0.0, "n_rejected": float(len(rejected))}
    return {"regret": statistics.fmean(rejected) - statistics.fmean(taken), "n_rejected": float(len(rejected))}


# ---------------------------------------------------------------- registry, promotion, evidence

@dataclass(frozen=True)
class Preregistration:
    hypothesis: str
    entry_rule: str
    exit_rule: str
    risk_budget: float
    comparison: str
    data_cutoff: str
    sample_size: int

    def complete(self) -> bool:
        return all([self.hypothesis, self.entry_rule, self.exit_rule, self.comparison, self.data_cutoff]) \
            and self.risk_budget > 0 and self.sample_size > 0


@dataclass
class StrategyRegistry:
    """FB-2202: one registry; code/config hashes immutable per version."""

    rows: dict[str, dict] = field(default_factory=dict)

    def register(self, name: str, version: str, code: str, config: dict, cost_version: str, desk: str,
                 prereg: Preregistration) -> str:
        if not prereg.complete():
            raise ReportError("preregistration incomplete (FB-2201)")
        key = f"{name}@{version}"
        h = hashlib.sha256((code + json.dumps(config, sort_keys=True)).encode()).hexdigest()
        if key in self.rows and self.rows[key]["hash"] != h:
            raise ReportError("changed code/config needs a new version")
        self.rows.setdefault(key, {"hash": h, "cost_version": cost_version, "desk": desk,
                                   "status": "SHADOW", "capital": 0.0})
        return h


def compare_all(strategy: float, *, cash: float, benchmark: float, naive: float, champion: float,
                momentum_beta: float, momentum_return: float) -> dict[str, float]:
    """FB-2203: excess vs each control, with momentum exposure removed from selection alpha."""
    return {"vs_cash": strategy - cash, "vs_benchmark": strategy - benchmark, "vs_naive": strategy - naive,
            "vs_champion": strategy - champion,
            "selection_alpha": strategy - benchmark - momentum_beta * momentum_return}


def evidence_bundle(items: dict[str, object]) -> dict:
    """FB-2208/FB-1111: hash-stamped bundle; attestation is a separate field never inferred."""
    body = json.dumps(items, sort_keys=True, default=str)
    return {"items": items, "sha256": hashlib.sha256(body.encode()).hexdigest(),
            "attestation": items.get("attestation", "NONE")}


def health(feed_uptime: float, lag_ms: float, quote_gaps: int, backlog: int, ledger_ok: bool,
           pending_mf: int, expiring_options: int, stale_marks: int) -> dict:
    """FB-2305 health screen data with an overall status."""
    bad = [k for k, v in (("feed", feed_uptime < 0.99), ("lag", lag_ms > 1000), ("gaps", quote_gaps > 0),
                          ("backlog", backlog > 100), ("ledger", not ledger_ok), ("stale", stale_marks > 0)) if v]
    return {"status": "DEGRADED" if bad else "OK", "problems": bad, "pending_mf": pending_mf,
            "expiring_options": expiring_options}


def manual_action(action: str) -> str:
    """FB-1104: manual sleeve actions; none authorises a physical order."""
    if action not in ("APPROVE", "REJECT", "WATCH", "NO_TRADE"):
        raise ReportError(f"unknown action {action}")
    return f"PAPER_{action}"
