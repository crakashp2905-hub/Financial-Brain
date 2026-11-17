"""Research study tooling for ETF, MF, index and Four-Node studies (sections 5, 6, 12, 18, 20, 24, 25).

These are building blocks. Running a decisive study is a new look and must be preregistered in the
trial ledger first (``Registration``); nothing here records a verdict.

* Rule signals: moving-average trend, mean reversion, volatility-scaled size (FB-0502).
* Cost plausibility gate for short-horizon ETF studies (FB-0504).
* Return decomposition: tracking effect vs selection against a benchmark (FB-0506, FB-0511, FB-1204).
* Stress scenarios (FB-0509). Fund persistence (FB-0605). Peer/category comparison (FB-0310,
  FB-0611, FB-1205, FB-2006).
* Baselines: no trade, random matched-exposure, opening-range, simple breakout (FB-2538).
* Cost model with spread, latency drift, commissions, taxes (FB-2539).
* Multiple-testing corrections and parameter stability (FB-2540).
* Direction and P&L scored separately (FB-2427). Cross-index features (FB-2409).
* Telemetry (FB-1807). Streaming/replay parity (FB-2534).
"""
from __future__ import annotations

import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field


class StudyError(ValueError):
    pass


# ---------------------------------------------------------------- registration

@dataclass(frozen=True)
class Registration:
    study_id: str
    hypothesis: str
    universe: tuple[str, ...]
    params: tuple[tuple[str, float], ...]
    cost_model: str
    gate: str
    registered_at: str
    ledger_entry: str | None = None

    def runnable(self) -> bool:
        """Only a study written to the trial ledger may produce a decisive result."""
        return self.ledger_entry is not None


# ---------------------------------------------------------------- rule signals

def sma(xs: Sequence[float], n: int) -> list[float | None]:
    return [None if i + 1 < n else sum(xs[i + 1 - n:i + 1]) / n for i in range(len(xs))]


def ma_trend(closes: Sequence[float], fast: int, slow: int) -> list[int]:
    f, s = sma(closes, fast), sma(closes, slow)
    return [0 if a is None or b is None else (1 if a > b else -1) for a, b in zip(f, s, strict=True)]


def mean_reversion(closes: Sequence[float], n: int, z: float) -> list[int]:
    out = []
    for i in range(len(closes)):
        if i + 1 < n:
            out.append(0)
            continue
        w = closes[i + 1 - n:i + 1]
        m, sd = statistics.fmean(w), statistics.pstdev(w)
        out.append(0 if sd == 0 else (1 if (closes[i] - m) / sd < -z else -1 if (closes[i] - m) / sd > z else 0))
    return out


def vol_scaled_size(target_vol: float, realised_vol: float, cap: float) -> float:
    if realised_vol <= 0:
        return 0.0
    return min(cap, target_vol / realised_vol)


def short_horizon_plausible(expected_move_bps: float, spread_bps: float, fees_bps: float,
                            depth_ok: bool) -> bool:
    return depth_ok and expected_move_bps > 2 * (spread_bps + fees_bps)


# ---------------------------------------------------------------- decomposition and stress

def decompose(price_return: float, nav_return: float, benchmark_return: float) -> dict[str, float]:
    """price - benchmark = (price - NAV) [market friction] + (NAV - benchmark) [tracking]."""
    return {"friction": price_return - nav_return, "tracking": nav_return - benchmark_return,
            "total_vs_benchmark": price_return - benchmark_return}


def excess_vs_matched(strategy: Sequence[float], benchmark: Sequence[float], beta: float) -> float:
    """Mean excess over a risk-matched (beta-scaled) benchmark holding."""
    return statistics.fmean(s - beta * b for s, b in zip(strategy, benchmark, strict=True))


STRESS = {
    "global_hours_gap": {"underlying": -0.04, "spread_mult": 3.0},
    "commodity_gap": {"underlying": -0.06, "spread_mult": 2.0},
    "stale_inav": {"underlying": 0.0, "spread_mult": 1.5},
    "liquidity_collapse": {"underlying": -0.02, "spread_mult": 10.0},
}


def stress_pnl(value: float, spread_bps: float, scenario: str) -> float:
    s = STRESS[scenario]
    return value * s["underlying"] - value * spread_bps * s["spread_mult"] / 1e4


def persistence(ranks_t0: dict[str, int], ranks_t1: dict[str, int], top: int) -> float:
    """Share of top-``top`` funds in one window that stay in the top in the next."""
    a = {k for k, r in ranks_t0.items() if r <= top}
    b = {k for k, r in ranks_t1.items() if r <= top}
    return len(a & b) / len(a) if a else 0.0


def peer_percentile(value: float, peers: Sequence[float]) -> float:
    if not peers:
        raise StudyError("no peers")
    return sum(p <= value for p in peers) / len(peers)


# ---------------------------------------------------------------- baselines and costs

def random_matched(n_bars: int, n_trades: int, seed: int) -> list[int]:
    rng = random.Random(seed)
    idx = set(rng.sample(range(n_bars), min(n_trades, n_bars)))
    return [1 if i in idx else 0 for i in range(n_bars)]


def opening_range_breakout(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
                           k: int) -> list[int]:
    if len(closes) <= k:
        return [0] * len(closes)
    hi, lo = max(highs[:k]), min(lows[:k])
    return [0] * k + [1 if c > hi else -1 if c < lo else 0 for c in closes[k:]]


@dataclass(frozen=True)
class CostModel:
    spread_bps: float
    commission_bps: float
    tax_bps: float
    latency_drift_bps: float

    def round_trip_bps(self) -> float:
        return self.spread_bps + 2 * self.commission_bps + self.tax_bps + 2 * self.latency_drift_bps


# ---------------------------------------------------------------- multiple testing and stability

def holm(pvalues: dict[str, float], alpha: float) -> set[str]:
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    passed = set()
    for i, (k, p) in enumerate(items):
        if p > alpha / (len(items) - i):
            break
        passed.add(k)
    return passed


def benjamini_hochberg(pvalues: dict[str, float], q: float) -> set[str]:
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m, cutoff = len(items), -1
    for i, (_, p) in enumerate(items, start=1):
        if p <= q * i / m:
            cutoff = i
    return {k for k, _ in items[:cutoff]} if cutoff > 0 else set()


def stable(neighbourhood: Sequence[float], *, min_share_positive: float) -> bool:
    """A parameter is stable when most of its neighbours are also positive (not a lone spike)."""
    return bool(neighbourhood) and sum(x > 0 for x in neighbourhood) / len(neighbourhood) >= min_share_positive


# ---------------------------------------------------------------- separate scoring

def direction_and_pnl(pred: Sequence[str], actual: Sequence[str], pnl: Sequence[float]) -> dict:
    classes = sorted(set(actual))
    recalls = [sum(p == a == c for p, a in zip(pred, actual, strict=True)) /
               max(1, sum(a == c for a in actual)) for c in classes]
    return {"balanced_accuracy": statistics.fmean(recalls) if recalls else 0.0,
            "net_pnl": sum(pnl), "trades": sum(1 for x in pnl if x != 0)}


def cross_index_features(ret_a: Sequence[float], ret_b: Sequence[float], n: int) -> dict[str, float]:
    """Divergence and rolling relative strength from returns known so far (FB-2409)."""
    if len(ret_a) < n or len(ret_b) < n:
        raise StudyError("not enough history")
    a, b = ret_a[-n:], ret_b[-n:]
    return {"divergence": a[-1] - b[-1], "relative_strength": sum(a) - sum(b)}


# ---------------------------------------------------------------- telemetry and parity

@dataclass
class Telemetry:
    latencies_ms: list[float] = field(default_factory=list)
    expired: int = 0
    missed: int = 0
    duplicates: int = 0
    issued: int = 0
    covered_bars: int = 0
    expected_bars: int = 0

    def summary(self) -> dict[str, float]:
        lat = sorted(self.latencies_ms)
        p99 = lat[min(len(lat) - 1, int(0.99 * len(lat)))] if lat else 0.0
        n = max(self.issued, 1)
        return {"p99_ms": p99, "expiry_rate": self.expired / n, "missed_rate": self.missed / n,
                "duplicate_rate": self.duplicates / n,
                "coverage": self.covered_bars / self.expected_bars if self.expected_bars else 0.0}


def replay_parity(stream: Callable[[Sequence[float]], list], batch: Callable[[Sequence[float]], list],
                  data: Sequence[float]) -> bool:
    """Streaming and batch evaluation of identical observations must emit identical events."""
    online: list = []
    for i in range(1, len(data) + 1):
        out = stream(data[:i])
        online.extend(e for e in out if e not in online)
    return online == batch(data)
