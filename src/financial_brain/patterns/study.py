"""Research study protocol and controls (stages 10-11: CP-0154..0188, offline).

Nothing here consumes a protected trial or the 2026Q4 holdout. The budget guard
refuses any P&L evaluation on protected data while the 2026Q4 budget is spent
(13/13, per the preregistration memory). Synthetic examples are labelled as such.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from financial_brain.patterns.spec import canonical_json, sha256_hex

PROTECTED_BUDGET_2026Q4 = {"budget": 13, "spent": 13}


class BudgetExhausted(RuntimeError):
    """A protected trial was requested with no remaining preregistered budget."""


class ProspectiveViolation(ValueError):
    """An observation pre-dates its registration: backfilled, not forward evidence."""


@dataclass(frozen=True)
class StudySpec:
    """Frozen description of one study; the SHA-256 changes if any parameter does (CP-0154)."""

    family: str
    variants: tuple[str, ...]
    periods: tuple[str, str]
    entry: str
    exit_horizons: tuple[int, ...]
    cost_bps: float
    benchmarks: tuple[str, ...]
    controls: tuple[str, ...] = ()
    protected: bool = False

    def __post_init__(self) -> None:
        if self.exit_horizons != tuple(sorted(set(self.exit_horizons))) or not self.exit_horizons:
            raise ValueError("exit horizons must be a non-empty sorted set")
        if any(h <= 0 for h in self.exit_horizons):
            raise ValueError("exit horizons must be positive")
        if self.entry != "next_open":
            raise ValueError("only next-open entry is permitted (CP-0158)")
        if any("ultimate" in h.lower() for h in self.benchmarks + self.controls + (self.entry,)):
            raise ValueError("ultimate-high/low exits are not permitted (CP-0159)")
        if not math.isfinite(self.cost_bps) or self.cost_bps < 0:
            raise ValueError("cost_bps must be finite and non-negative")

    @property
    def sha256(self) -> str:
        return sha256_hex(canonical_json(self.__dict__))

    @property
    def trial_family_size(self) -> int:
        """Every examined variant counts toward the family (CP-0155, CP-0167)."""
        return len(self.variants)


def require_budget(spec: StudySpec, evaluate_pnl: bool) -> None:
    """Refuse P&L evaluation on protected data while the preregistered budget is spent (CP-0162)."""
    if evaluate_pnl and spec.protected:
        remaining = PROTECTED_BUDGET_2026Q4["budget"] - PROTECTED_BUDGET_2026Q4["spent"]
        if remaining <= 0:
            raise BudgetExhausted("2026Q4 trial budget is 13/13 spent; protected P&L evaluation is refused")


def dry_run_report(spec: StudySpec, sample_counts: dict[str, int]) -> dict[str, Any]:
    """Validate spec and sample coverage without computing any return (CP-0163)."""
    return {
        "spec_sha256": spec.sha256,
        "trial_family_size": spec.trial_family_size,
        "sample_counts": dict(sorted(sample_counts.items())),
        "returns_computed": False,
        "eligible": all(v > 0 for v in sample_counts.values()) if sample_counts else False,
    }


def census(formations: list[Any]) -> dict[str, int]:
    """Descriptive counts of events by state; no forward returns are computed (CP-0157)."""
    counts: dict[str, int] = {}
    for f in formations:
        key = f.state_at(10**9) or "NONE"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def entry_index(confirmed_index: int) -> int:
    """Next-open entry: the earliest executable bar follows the confirmation bar (CP-0158)."""
    return confirmed_index + 1


def fixed_exit_index(entry: int, horizon: int) -> int:
    """Fixed holding-period exit only (CP-0159)."""
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    return entry + horizon


@dataclass(frozen=True)
class Panel:
    """Candidate-clock and confirmation-clock counts with explicit at-risk denominators (CP-0160/0161)."""

    candidates: int
    confirmed: int
    failed: int
    still_open: int
    confirmed_rate_of_candidates: float | None
    missing_outcomes: int


def clock_panels(formations: list[Any], *, at: int) -> Panel:
    cands = [f for f in formations if f.candidate_known_index <= at]
    conf = [f for f in cands if f.confirmed_index is not None and f.confirmed_index <= at]
    fail = [f for f in cands if f.failed_index is not None and f.failed_index <= at]
    open_ = len(cands) - len(conf) - len(fail)
    rate = len(conf) / len(cands) if cands else None
    return Panel(len(cands), len(conf), len(fail), open_, rate, missing_outcomes=open_)


def purged_split(n: int, *, train_end: int, embargo: int) -> tuple[range, range]:
    """Chronological train/test with an embargo of at least the label overlap (CP-0180)."""
    if not 0 < train_end < n or embargo < 0:
        raise ValueError("invalid split")
    test_start = train_end + embargo
    if test_start >= n:
        raise ValueError("embargo leaves no test data")
    return range(0, train_end), range(test_start, n)


def block_bootstrap_ci(values_by_cluster: dict[str, list[float]], *, reps: int = 2000, seed: int = 7,
                       alpha: float = 0.05) -> tuple[float, float, float]:
    """Mean with a cluster-block bootstrap interval (symbol clusters kept together) (CP-0165)."""
    clusters = [v for _, v in sorted(values_by_cluster.items()) if v]
    if not clusters:
        raise ValueError("no clusters")
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(reps):
        picks = rng.integers(0, len(clusters), size=len(clusters))
        flat = [x for i in picks for x in clusters[int(i)]]
        means.append(sum(flat) / len(flat))
    flat_all = [x for c in clusters for x in c]
    point = sum(flat_all) / len(flat_all)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return float(point), float(lo), float(hi)


def benjamini_hochberg(pvalues: dict[str, float], q: float = 0.1) -> set[str]:
    """Names whose p-values survive FDR control at level q (CP-0184)."""
    items = sorted(pvalues.items(), key=lambda kv: (kv[1], kv[0]))
    m = len(items)
    cutoff = -1
    for rank, (_, p) in enumerate(items, start=1):
        if p <= q * rank / m:
            cutoff = rank
    return {name for name, _ in items[:cutoff]} if cutoff > 0 else set()


def matched_controls(candidates: list[dict[str, float]], pool: list[dict[str, float]],
                     keys: tuple[str, ...]) -> list[int | None]:
    """Nearest control by standardised features; None if no match (CP-0172)."""
    if not pool:
        return [None] * len(candidates)
    scale = {k: (max(abs(p[k]) for p in pool) or 1.0) for k in keys}
    out: list[int | None] = []
    for c in candidates:
        best, best_d = None, math.inf
        for j, p in enumerate(pool):
            d = sum(((c[k] - p[k]) / scale[k]) ** 2 for k in keys)
            if d < best_d:
                best, best_d = j, d
        out.append(best)
    return out


def time_shift_placebo(eligible_days: list[int], n: int, *, shift: int) -> list[int]:
    """Reproducible placebo dates, shifted within the eligible set only (CP-0174)."""
    s = set(eligible_days)
    return [d + shift for d in eligible_days if d + shift in s and 0 <= d + shift < n]


def incremental_value(pattern_net: list[float], baseline_net: list[float]) -> float:
    """Matched incremental net return: pattern-filtered minus baseline on the same decisions (CP-0177)."""
    if len(pattern_net) != len(baseline_net) or not pattern_net:
        raise ValueError("matched series required")
    return sum(pattern_net) / len(pattern_net) - sum(baseline_net) / len(baseline_net)


def stress_matrix(net_returns: list[float], *, spreads_bps: tuple[float, ...]) -> dict[float, float]:
    """Mean net return under adverse spread assumptions (CP-0183)."""
    out: dict[float, float] = {}
    for s in spreads_bps:
        out[s] = sum(r - s / 1e4 for r in net_returns) / len(net_returns)
    return out


def assert_prospective(observed_at_utc: str, registered_at_utc: str) -> None:
    """Refuse backfilled observations as forward evidence (CP-0169)."""
    from datetime import datetime

    if datetime.fromisoformat(observed_at_utc) < datetime.fromisoformat(registered_at_utc):
        raise ProspectiveViolation("observation predates registration: backfilled, not forward evidence")


def synthetic_null(seed: int, n: int = 500) -> list[float]:
    """SYNTHETIC: zero-mean noise. Labelled, not market evidence (CP-0170)."""
    rng = np.random.default_rng(seed)
    return [float(x) for x in rng.normal(0.0, 0.01, n)]


def synthetic_edge(seed: int, n: int = 500, edge: float = 0.002) -> list[float]:
    """SYNTHETIC: injected positive mean for harness testing only (CP-0170)."""
    rng = np.random.default_rng(seed)
    return [float(x) for x in rng.normal(edge, 0.01, n)]


REPORT_FIELDS = ("statistic", "denominator", "benchmark", "eligibility", "caveats", "next_gate", "lineage")


def report_record(**fields: Any) -> dict[str, Any]:
    """Provenance-rich result record; omitting any field is an error (CP-0171)."""
    missing = [f for f in REPORT_FIELDS if f not in fields]
    if missing:
        raise ValueError(f"report missing required fields: {missing}")
    return {f: fields[f] for f in REPORT_FIELDS}


@dataclass
class VariantRegistry:
    """Every examined variant, with thresholds, for the data-snooping audit (CP-0167)."""

    variants: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, **params: Any) -> None:
        self.variants.append({"name": name, **params})

    def audit(self) -> dict[str, Any]:
        return {"family_size": len(self.variants), "variants": self.variants}


def reality_check_adapter(returns: dict[str, list[float]], *, reps: int = 200) -> dict[str, Any]:
    """Run the repository's existing White Reality Check on variant returns (CP-0166).

    Columns are variants and rows are time-ordered observations, as the underlying
    implementation expects. Nothing is re-implemented here.
    """
    import pandas as pd

    from financial_brain.evaluation.family_validation import reality_check

    frame = pd.DataFrame(returns)
    return reality_check(frame, reps=reps)


def small_sample_label(n: int, *, minimum: int = 30) -> str:
    """Rare or small samples are labelled LOW_CONFIDENCE and never yield probabilities (CP-0147)."""
    if n < 0:
        raise ValueError("sample size must be non-negative")
    return "LOW_CONFIDENCE" if n < minimum else "SAMPLE_OK"


def shuffled_direction_control(signs: list[int], *, seed: int = 11) -> list[int]:
    """Reproducible direction shuffle that keeps the number of longs and shorts (CP-0175)."""
    if any(s not in (-1, 1) for s in signs):
        raise ValueError("signs must be -1 or +1")
    rng = np.random.default_rng(seed)
    out = list(signs)
    rng.shuffle(out)
    return [int(x) for x in out]


def matched_baseline(candidate_ids: list[int], all_ids: list[int]) -> list[int]:
    """Baseline drawn from the exact same candidate opportunity set (CP-0176)."""
    universe = set(all_ids)
    missing = [c for c in candidate_ids if c not in universe]
    if missing:
        raise ValueError(f"candidates outside the opportunity set: {missing}")
    return sorted(candidate_ids)


def neutralize_exposure(returns: list[float], market: list[float]) -> tuple[list[float], float]:
    """OLS residuals after removing market beta; returns (residuals, beta) (CP-0178)."""
    if len(returns) != len(market) or len(returns) < 3:
        raise ValueError("aligned series of length >= 3 required")
    x = np.asarray(market, dtype=float)
    y = np.asarray(returns, dtype=float)
    beta = float(np.cov(x, y, ddof=1)[0, 1] / np.var(x, ddof=1)) if np.var(x, ddof=1) > 0 else 0.0
    alpha = float(y.mean() - beta * x.mean())
    residuals = y - (alpha + beta * x)
    return [float(r) for r in residuals], beta


def ablation_variants(toggles: tuple[str, ...]) -> list[dict[str, bool]]:
    """Every on/off combination of named toggles; the count is part of the multiplicity (CP-0179)."""
    if len(set(toggles)) != len(toggles):
        raise ValueError("toggle names must be unique")
    variants: list[dict[str, bool]] = [{}]
    for name in toggles:
        variants = [dict(v, **{name: flag}) for v in variants for flag in (False, True)]
    return variants


def turnover(prev_weights: dict[str, float], new_weights: dict[str, float]) -> float:
    """One-way turnover: half the sum of absolute weight changes (CP-0181)."""
    names = set(prev_weights) | set(new_weights)
    return 0.5 * sum(abs(new_weights.get(n, 0.0) - prev_weights.get(n, 0.0)) for n in names)


def pattern_vs_no_trade(pattern_net: list[float]) -> dict[str, float]:
    """Selective filtering is valued against cash/no trade on realised net P&L (CP-0182)."""
    count = len(pattern_net)
    total = sum(pattern_net)
    return {"trades": float(count), "net_total": total, "net_vs_cash": total}


def split_selected_rejected(values: list[float], selected: list[bool]) -> dict[str, float]:
    """Pre/post breakdown: what the filter kept versus what it rejected (CP-0186)."""
    if len(values) != len(selected):
        raise ValueError("values and selection must align")
    kept = [v for v, s in zip(values, selected, strict=True) if s]
    dropped = [v for v, s in zip(values, selected, strict=True) if not s]
    return {
        "kept_mean": float(np.mean(kept)) if kept else 0.0,
        "rejected_mean": float(np.mean(dropped)) if dropped else 0.0,
        "opportunity_cost": float(np.sum(dropped)) if dropped else 0.0,
    }


def decompose(total: float, *, entry: float, exit_: float, sizing: float, pattern: float, tol: float = 1e-9) -> dict[str, float]:
    """Counterfactual attribution: components must reconcile to the total with no double counting (CP-0188)."""
    parts = entry + exit_ + sizing + pattern
    if abs(parts - total) > tol:
        raise ValueError(f"components sum to {parts}, not {total}")
    return {"entry": entry, "exit": exit_, "sizing": sizing, "pattern": pattern, "total": total}
