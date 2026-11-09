"""Strategy research and monitoring (FB-0438, FB-0440, FB-0445, FB-0450, FB-0454, FB-0455, FB-0465, FB-0466,
FB-0469, FB-0470, FB-0473, FB-0475, FB-0476).

* ``cross_sectional_momentum`` ranks assets by return only when enough of the universe has data.
* ``donchian_signal`` is a channel breakout using the prior ``n`` bars only, so no same-bar entry.
* ``failed_breakout_candidate`` flags a close above a level that returns below it within ``k`` bars. It is a candidate
  for review, not a signal.
* ``carry_annualised`` is the annualised roll yield between a near and far contract.
* ``StrategyCard`` requires rationale, edge hypothesis and invalidation, and carries an evidence tier and the known
  negative results.
* ``decompose`` splits mean return by regime and by market.
* ``param_distribution`` counts which parameter values walk-forward folds chose.
* ``quantile_coverage`` is the share of outcomes at or below their forecast quantile.
* ``drift_alert`` flags a mean shift in current data against a baseline, in standard-error units.
* ``regret`` is the best logged alternative's P&L minus the chosen P&L.
* ``forward_evaluation`` refuses forward evidence that shares any id with training.
* ``publish_trials`` returns every trial, failed ones included. A trial without an outcome is refused.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import mean, pstdev

from financial_brain.kernel.contracts import EvidenceTier


def cross_sectional_momentum(
    returns: Mapping[str, float], universe: set[str], min_coverage: float
) -> tuple[list[tuple[str, float]], bool]:
    if not universe:
        raise ValueError("universe is empty")
    coverage = len(set(returns) & universe) / len(universe)
    if coverage < min_coverage:
        return [], False
    ranked = sorted(((s, r) for s, r in returns.items() if s in universe), key=lambda x: (-x[1], x[0]))
    return ranked, True


def donchian_signal(close: Sequence[float], high: Sequence[float], low: Sequence[float], n: int) -> list[int]:
    if n < 1:
        raise ValueError("n must be positive")
    out = [0] * len(close)
    for i in range(n, len(close)):
        upper = max(high[i - n : i])
        lower = min(low[i - n : i])
        if close[i] > upper:
            out[i] = 1
        elif close[i] < lower:
            out[i] = -1
    return out


def failed_breakout_candidate(closes: Sequence[float], level: float, k: int) -> bool:
    if k < 1:
        raise ValueError("k must be positive")
    for i, c in enumerate(closes):
        if c > level:
            after = closes[i + 1 : i + 1 + k]
            return len(after) == k and all(x < level for x in after)
    return False


def carry_annualised(near: float, far: float, days_between: int) -> float:
    if days_between <= 0 or near <= 0:
        raise ValueError("days and near price must be positive")
    return (far - near) / near * 365.0 / days_between


@dataclass(frozen=True)
class StrategyCard:
    name: str
    rationale: str
    edge_hypothesis: str
    invalidation: str
    evidence_tier: EvidenceTier
    negative_results: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in ("name", "rationale", "edge_hypothesis", "invalidation"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"strategy card needs a non-empty {field_name}")


def decompose(
    returns: Sequence[float], regimes: Sequence[str], markets: Sequence[str]
) -> dict[str, dict[str, float]]:
    if not (len(returns) == len(regimes) == len(markets)):
        raise ValueError("returns, regimes and markets must align")
    by_regime: dict[str, list[float]] = {}
    by_market: dict[str, list[float]] = {}
    for r, reg, mkt in zip(returns, regimes, markets, strict=True):
        by_regime.setdefault(reg, []).append(r)
        by_market.setdefault(mkt, []).append(r)
    return {
        "regime": {k: mean(v) for k, v in sorted(by_regime.items())},
        "market": {k: mean(v) for k, v in sorted(by_market.items())},
    }


def param_distribution(chosen: Sequence[str]) -> tuple[dict[str, int], str | None]:
    counts = Counter(chosen)
    mode = counts.most_common(1)[0][0] if counts else None
    return dict(sorted(counts.items())), mode


def quantile_coverage(forecasts: Sequence[float], outcomes: Sequence[float]) -> float:
    if len(forecasts) != len(outcomes) or not forecasts:
        raise ValueError("forecasts and outcomes must align and be non-empty")
    return sum(1 for f, o in zip(forecasts, outcomes, strict=True) if o <= f) / len(forecasts)


def drift_alert(baseline: Sequence[float], current: Sequence[float], z_threshold: float) -> bool:
    if len(baseline) < 2 or not current:
        raise ValueError("need a baseline of at least two and some current data")
    sd = pstdev(baseline)
    if sd == 0:
        raise ValueError("baseline has no variation")
    z = (mean(current) - mean(baseline)) / (sd / len(current) ** 0.5)
    return abs(z) >= z_threshold


def regret(chosen_pnl: float, alternatives: Mapping[str, float]) -> float:
    if not alternatives:
        raise ValueError("regret needs logged alternatives")
    return max(alternatives.values()) - chosen_pnl


def forward_evaluation(train_ids: set[str], forward_ids: set[str], forward_returns: Sequence[float]) -> float:
    overlap = train_ids & forward_ids
    if overlap:
        raise ValueError(f"forward evidence overlaps training: {sorted(overlap)}")
    if not forward_returns:
        raise ValueError("no forward returns")
    return mean(forward_returns)


def publish_trials(trials: Sequence[Mapping[str, str]]) -> list[Mapping[str, str]]:
    for t in trials:
        if "outcome" not in t:
            raise ValueError(f"trial {t.get('id', '?')} has no outcome; every trial is published")
    return sorted(trials, key=lambda t: t["id"])
