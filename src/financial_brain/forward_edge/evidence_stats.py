"""Statistical confirmation and evidence tiers (FE-0091 .. FE-0103).

No synthetic result here proves alpha. The verdict function is deliberately hard to pass: it needs enough eligible
independent sessions, a positive lower confidence bound under dependence-robust inference, robustness under cost stress,
no single-regime concentration, an adequate predeclared sample design, and an INDEPENDENT_FORWARD evidence tier.
"""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from math import sqrt
from collections.abc import Mapping, Sequence

import numpy as np

from financial_brain.forward_edge.evidence_labels import EvidenceLabelError, classify


class StatsError(ValueError):
    pass


# ---- FE-0091 daily returns ----

def daily_returns(equity: list[float], external_flows: list[float] | None = None) -> list[float]:
    """Daily net return on previous-close equity, adjusted for external cash flows. Cash days are included."""
    if len(equity) < 2:
        raise StatsError("need at least two equity points")
    flows = external_flows or [0.0] * (len(equity) - 1)
    if len(flows) != len(equity) - 1:
        raise StatsError("one flow per day required")
    out = []
    for i in range(1, len(equity)):
        base = equity[i - 1]
        if base <= 0:
            raise StatsError("previous equity must be positive")
        out.append((equity[i] - base - flows[i - 1]) / base)
    return out


# ---- FE-0092 / FE-0093 dependence-robust inference ----

def naive_se(x: list[float]) -> float:
    a = np.asarray(x, dtype=float)
    return float(a.std(ddof=1) / sqrt(len(a)))


def newey_west_se(x: list[float], lags: int) -> float:
    a = np.asarray(x, dtype=float)
    n = len(a)
    if n < 3 or lags < 0 or lags >= n:
        raise StatsError("invalid sample or lag")
    d = a - a.mean()
    lrv = float(d @ d) / n
    for lag in range(1, lags + 1):
        w = 1.0 - lag / (lags + 1.0)
        lrv += 2.0 * w * float(d[lag:] @ d[:-lag]) / n
    return sqrt(max(lrv, 0.0) / n)


def stationary_bootstrap_ci(x: list[float], *, resamples: int = 2000, mean_block: float = 5.0, seed: int = 0,
                            alpha: float = 0.05) -> tuple[float, float]:
    a = np.asarray(x, dtype=float)
    n = len(a)
    if n < 3:
        raise StatsError("too few observations")
    rng = np.random.default_rng(seed)
    p = 1.0 / mean_block
    means = np.empty(resamples)
    for b in range(resamples):
        idx = np.empty(n, dtype=int)
        idx[0] = rng.integers(n)
        for t in range(1, n):
            idx[t] = rng.integers(n) if rng.random() < p else (idx[t - 1] + 1) % n
        means[b] = a[idx].mean()
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def cluster_se(values: list[float], clusters: list[str]) -> float:
    """SE of the mean with observations clustered (by date): sums within clusters, so trade count is not sample size."""
    if len(values) != len(clusters) or not values:
        raise StatsError("values and clusters must align")
    sums: dict[str, float] = {}
    for v, c in zip(values, clusters):
        sums[c] = sums.get(c, 0.0) + v
    n = len(values)
    mean = sum(values) / n
    g = len(sums)
    if g < 2:
        raise StatsError("need at least two clusters")
    resid = [s - mean * sum(1 for c in clusters if c == k) for k, s in sums.items()]
    return sqrt(sum(r * r for r in resid) * g / (g - 1)) / n


# ---- FE-0094 multiplicity ----

def holm_adjust(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm step-down adjusted p-values. SPA / Reality Check are not implemented (tracker: PARTIAL)."""
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    out: dict[str, float] = {}
    running = 0.0
    for i, (name, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        out[name] = running
    return out


def adjust_registered_family(
    registered_trial_ids: Sequence[str],
    p_values: Mapping[str, float | None],
) -> dict:
    """Apply Holm correction across a frozen family, counting missing/failed trials as p=1.

    The registered identifier list is the denominator. A missing p-value does not
    disappear from the family; it is reported as unavailable and assigned p=1 for
    correction. Unknown p-value keys are rejected because they were not registered.
    """
    ids = list(registered_trial_ids)
    if not ids or any(not isinstance(value, str) or not value.strip() for value in ids):
        raise StatsError("registered family needs non-empty trial ids")
    if len(ids) != len(set(ids)):
        raise StatsError("registered trial ids must be unique")
    unknown = sorted(set(p_values) - set(ids))
    if unknown:
        raise StatsError(f"p-values supplied for unregistered trials: {unknown}")
    raw: dict[str, float] = {}
    missing = []
    for trial_id in ids:
        value = p_values.get(trial_id)
        if value is None:
            missing.append(trial_id)
            raw[trial_id] = 1.0
            continue
        p = float(value)
        if not np.isfinite(p) or not 0 <= p <= 1:
            raise StatsError(f"invalid p-value for {trial_id}")
        raw[trial_id] = p
    adjusted = holm_adjust(raw)
    family = sorted(ids)
    family_sha256 = hashlib.sha256(json.dumps(
        family, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()
    return {
        "method": "holm_step_down_familywise_v1",
        "family_size": len(ids),
        "family_sha256": family_sha256,
        "tested_count": len(ids) - len(missing),
        "missing_p_value_count": len(missing),
        "missing_trial_ids": sorted(missing),
        "trials": {trial_id: {
            "raw_p_value": p_values.get(trial_id),
            "p_value_used_for_adjustment": raw[trial_id],
            "adjusted_p_value": adjusted[trial_id],
        } for trial_id in family},
    }


# ---- FE-0095 / FE-0096 ----

def drawdown_stats(returns: list[float]) -> dict[str, float]:
    eq = np.cumprod(1.0 + np.asarray(returns, dtype=float))
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    dd = eq / peak - 1.0
    gains = sum(r for r in returns if r > 0)
    losses = -sum(r for r in returns if r < 0)
    return {"max_drawdown": float(dd.min()), "worst_day": float(min(returns)),
            "profit_factor": float(gains / losses) if losses > 0 else float("inf")}


def capacity_adjusted_return(gross_return: float, participation: float, impact_coeff: float) -> float:
    """Return degrades with participation (square-root impact). Recommended size is where it reaches zero."""
    if participation < 0:
        raise StatsError("participation must be non-negative")
    return gross_return - impact_coeff * sqrt(participation)


# ---- FE-0097 / FE-0098 / FE-0099 / FE-0100 / FE-0101 ----

def required_sessions(sigma: float, target_halfwidth: float, z: float = 1.96) -> int:
    if sigma <= 0 or target_halfwidth <= 0:
        raise StatsError("sigma and half-width must be positive")
    return int(np.ceil((z * sigma / target_halfwidth) ** 2))


def stress_robust(net_by_multiplier: dict[float, float], required: tuple[float, ...] = (1.0, 1.5, 2.0)) -> bool:
    return all(net_by_multiplier.get(m, float("-inf")) > 0 for m in required)


EXECUTION_STRESS_COSTS = (1.0, 1.5, 2.0)
EXECUTION_STRESS_FILL_RATES = (1.0, 0.75, 0.5)


def execution_stress_gate(net_by_cost_and_fill: dict[tuple[float, float], float] | None) -> dict:
    """Require all 1x/1.5x/2x cost and 100/75/50% fill-rate scenarios before promotion.

    Scenario economics must be computed from the frozen intention-to-trade population by the caller. Missing
    cells are evidence gaps; non-finite or non-positive net outcomes fail the gate. This function never invents
    fill outcomes or extrapolates them from the filled-only sample.
    """
    required = [(cost, fill) for cost in EXECUTION_STRESS_COSTS for fill in EXECUTION_STRESS_FILL_RATES]
    if net_by_cost_and_fill is None:
        return {"status": "MISSING", "missing": [f"{c:g}x@{f:.0%}" for c, f in required], "failed": []}
    missing, failed = [], []
    for cost, fill in required:
        key = (cost, fill)
        value = net_by_cost_and_fill.get(key)
        label = f"{cost:g}x@{fill:.0%}"
        if value is None or not np.isfinite(value):
            missing.append(label)
        elif value <= 0:
            failed.append(label)
    status = "FAIL" if failed else ("MISSING" if missing else "PASS")
    return {"status": status, "missing": missing, "failed": failed}


def best_block_share(returns: list[float], blocks: int) -> float:
    """Share of total positive P&L produced by the single best chronological block (regime concentration)."""
    if blocks < 2 or len(returns) < blocks:
        raise StatsError("need at least two blocks of data")
    size = len(returns) // blocks
    sums = [sum(returns[i * size:(i + 1) * size]) for i in range(blocks)]
    total_pos = sum(s for s in sums if s > 0)
    return max(sums) / total_pos if total_pos > 0 else 1.0


class StoppingPolicy:
    """Frozen checkpoints: peeking between them never promotes; a harm threshold can stop early."""

    def __init__(self, checkpoints: tuple[int, ...], harm_floor: float) -> None:
        if list(checkpoints) != sorted(set(checkpoints)) or not checkpoints:
            raise StatsError("checkpoints must be sorted, unique and non-empty")
        self.checkpoints = checkpoints
        self.harm_floor = harm_floor

    def evaluation_allowed(self, sessions: int) -> bool:
        return sessions in self.checkpoints

    def stop_for_harm(self, cumulative_return: float) -> bool:
        return cumulative_return <= self.harm_floor


# ---- FE-0102 tiers ----

class ClaimTier(str, Enum):
    HISTORICAL = "HISTORICAL"
    SHADOW = "SHADOW"
    LOCAL_PAPER = "LOCAL_PAPER"
    INDEPENDENT_FORWARD = "INDEPENDENT_FORWARD"
    DEPLOYMENT_VALIDATED = "DEPLOYMENT_VALIDATED"


def label_rows(rows: list[dict]) -> list[dict]:
    """Every chart/export row has a tier consistent with its source mode and evidence facts."""
    labelled = []
    for r in rows:
        if not isinstance(r.get("tier"), ClaimTier):
            raise StatsError("every row needs exactly one ClaimTier")
        mode = r.get("mode")
        if not isinstance(mode, str):
            raise StatsError("every row needs a declared evidence mode")
        try:
            evidence = classify(
                mode, market_observed=r.get("market_observed", False),
                pre_entry_sealed=r.get("pre_entry_sealed", False),
                session_complete=r.get("session_complete", False),
                custody_status=r.get("custody_status"),
            )
        except EvidenceLabelError as exc:
            raise StatsError(f"row evidence mode is invalid: {exc}") from exc
        expected: dict[ClaimTier, str] = {
            ClaimTier.HISTORICAL: "HISTORICAL_REPLAY",
            ClaimTier.SHADOW: "EXPLORATORY_SHADOW",
            ClaimTier.LOCAL_PAPER: "LOCAL_PAPER",
            ClaimTier.INDEPENDENT_FORWARD: "INDEPENDENTLY_ATTESTED_FORWARD",
        }
        if r["tier"] is ClaimTier.DEPLOYMENT_VALIDATED:
            raise StatsError("deployment validation cannot be asserted by the generic evidence-row exporter")
        expected_class = expected[r["tier"]]
        if evidence.value != expected_class:
            raise StatsError(f"tier {r['tier'].value} conflicts with evidence mode {evidence.value}")
        row = dict(r)
        existing = row.get("evidence_class")
        if existing is not None and existing != evidence.value:
            raise StatsError("caller evidence label conflicts with derived evidence mode")
        row["evidence_class"] = evidence.value
        row["independently_attested"] = evidence.value == "INDEPENDENTLY_ATTESTED_FORWARD"
        labelled.append(row)
    return labelled


# ---- FE-0103 falsification ----

FALSIFICATION_CHECKS = ("lookahead", "survivorship", "delisting", "factor_overlap", "rejected_trades", "missed_fills")


def falsification_report(results: dict[str, bool | None]) -> dict:
    """True = check passed, False = check failed, None/absent = no evidence. No evidence is INSUFFICIENT, never a win."""
    failed = [c for c in FALSIFICATION_CHECKS if results.get(c) is False]
    missing = [c for c in FALSIFICATION_CHECKS if results.get(c) is None]
    status = "FAIL" if failed else ("INSUFFICIENT_EVIDENCE" if missing else "NO_FALSIFICATION_FOUND")
    return {"status": status, "failed": failed, "missing": missing}


# ---- verdict ----

def promotion_verdict(*, daily_net_alpha: list[float], eligible_independent_sessions: int, tier: ClaimTier,
                      net_by_cost_multiplier: dict[float, float], best_block: float, sample_design_predeclared: bool,
                      falsification_status: str,
                      net_by_cost_and_fill: dict[tuple[float, float], float] | None = None,
                      min_sessions: int = 60, lags: int = 5, z: float = 1.96,
                      max_block_share: float = 0.6) -> dict:
    reasons: list[str] = []
    n = len(daily_net_alpha)
    mean = float(np.mean(daily_net_alpha)) if n else 0.0
    lcb = None
    if n >= 3 and lags < n:
        lcb = mean - z * newey_west_se(daily_net_alpha, min(lags, n - 1))
    if tier is not ClaimTier.INDEPENDENT_FORWARD:
        reasons.append("TIER_NOT_INDEPENDENT_FORWARD")
    if eligible_independent_sessions < min_sessions:
        reasons.append("BELOW_SESSION_FLOOR")
    if not sample_design_predeclared:
        reasons.append("SAMPLE_DESIGN_NOT_PREDECLARED")
    if falsification_status != "NO_FALSIFICATION_FOUND":
        reasons.append("FALSIFICATION_" + falsification_status)
    if best_block > max_block_share:
        reasons.append("REGIME_CONCENTRATION")
    if not stress_robust(net_by_cost_multiplier):
        reasons.append("FAILS_COST_STRESS")
    execution_stress = execution_stress_gate(net_by_cost_and_fill)
    if execution_stress["status"] == "MISSING":
        reasons.append("EXECUTION_STRESS_MISSING")
    elif execution_stress["status"] == "FAIL":
        reasons.append("FAILS_EXECUTION_STRESS")
    if lcb is None or lcb <= 0:
        reasons.append("LOWER_BOUND_NOT_POSITIVE")
    if not reasons:
        return {"verdict": "PROMOTION_GRADE", "mean": mean, "lower_bound": lcb, "reasons": [],
                "execution_stress": execution_stress}
    hard_fail = (mean <= 0 and n >= 3 or "FAILS_COST_STRESS" in reasons
                 or "FAILS_EXECUTION_STRESS" in reasons or "FALSIFICATION_FAIL" in reasons)
    return {"verdict": "FAIL" if hard_fail else "INSUFFICIENT_EVIDENCE", "mean": mean, "lower_bound": lcb,
            "reasons": reasons, "execution_stress": execution_stress}
