"""Risk limits and learning controls (FB-0274, FB-0276, FB-0278, FB-0281, FB-0486, FB-0487, FB-0488, FB-0489,
FB-0490, FB-0494, FB-0495, FB-0497, FB-0498).

* ``limit_breaches`` checks liquidity (ADV share), concentration and gap risk against limits.
* ``aggregate_greeks`` sums quantity-weighted delta, gamma and vega over positions.
* ``currency_breaches`` lists correlated currency groups whose summed exposure exceeds a limit.
* ``emergency_policy`` unwinds the unhedged excess when one leg is filled without its hedge beyond the limit.
* ``rejection_learning`` averages the missed P&L of rejected trades only.
* ``feature_drift`` reports the mean shift of each feature between baseline and current data.
* ``sanitize_rows`` drops training rows with NaN features or labels other than 0 and 1, and counts the drops.
* ``paired_eval`` compares champion and challenger errors on the same cases.
* ``holdout_guard`` refuses any holdout case already used for training or tuning.
* ``missing_features`` lists features an artifact needs that the runtime does not provide.
* ``explain_prediction`` shows prediction, uncertainty, cost and risk together; a missing part is refused.
* ``drift_response`` abstains or reduces paper risk when drift passes a threshold.
* ``budget_guard`` refuses trials beyond the exploration budget and reports what remains.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from statistics import mean


def limit_breaches(
    adv_share: float,
    max_adv_share: float,
    weight: float,
    max_weight: float,
    gap_pct: float,
    max_gap_pct: float,
) -> list[str]:
    out = []
    if adv_share > max_adv_share:
        out.append("ADV")
    if weight > max_weight:
        out.append("CONCENTRATION")
    if abs(gap_pct) > max_gap_pct:
        out.append("GAP")
    return out


def aggregate_greeks(positions: Sequence[tuple[int, float, float, float]]) -> dict[str, float]:
    totals = {"delta": 0.0, "gamma": 0.0, "vega": 0.0}
    for qty, delta, gamma, vega in positions:
        totals["delta"] += qty * delta
        totals["gamma"] += qty * gamma
        totals["vega"] += qty * vega
    return totals


def currency_breaches(
    exposures: Mapping[str, float], groups: Mapping[str, Sequence[str]], limit: float
) -> dict[str, float]:
    out = {}
    for name, members in groups.items():
        total = sum(abs(exposures.get(c, 0.0)) for c in members)
        if total > limit:
            out[name] = total
    return out


def emergency_policy(filled_units: int, opposite_units: int, max_unhedged: int) -> str:
    unhedged = abs(filled_units - opposite_units)
    return "UNWIND_EXCESS" if unhedged > max_unhedged else "HOLD"


def rejection_learning(records: Sequence[tuple[bool, float]]) -> float | None:
    missed = [pnl for rejected, pnl in records if rejected]
    return mean(missed) if missed else None


def feature_drift(baseline: Mapping[str, Sequence[float]], current: Mapping[str, Sequence[float]]) -> dict[str, float]:
    return {f: mean(current[f]) - mean(baseline[f]) for f in baseline if f in current}


def sanitize_rows(rows: Sequence[tuple[Sequence[float], int]]) -> tuple[list[tuple[Sequence[float], int]], int]:
    kept = []
    for features, label in rows:
        if label not in (0, 1) or any(math.isnan(x) for x in features):
            continue
        kept.append((features, label))
    return kept, len(rows) - len(kept)


def paired_eval(champion_errors: Sequence[float], challenger_errors: Sequence[float]) -> dict[str, float]:
    if len(champion_errors) != len(challenger_errors) or not champion_errors:
        raise ValueError("errors must align and be non-empty")
    diffs = [c - h for c, h in zip(champion_errors, challenger_errors, strict=True)]
    return {"mean_improvement": mean(diffs), "challenger_wins": float(sum(1 for d in diffs if d > 0))}


def holdout_guard(used_ids: set[str], requested: set[str]) -> bool:
    overlap = used_ids & requested
    if overlap:
        raise ValueError(f"holdout cases already used: {sorted(overlap)}")
    return True


def missing_features(artifact_features: Sequence[str], runtime_features: Sequence[str]) -> list[str]:
    return sorted(set(artifact_features) - set(runtime_features))


def explain_prediction(prediction: float | None, uncertainty: float | None, cost: float | None, risk: float | None) -> str:
    parts = {"prediction": prediction, "uncertainty": uncertainty, "cost": cost, "risk": risk}
    missing = [k for k, v in parts.items() if v is None]
    if missing:
        raise ValueError(f"explanation missing {missing}")
    return ", ".join(f"{k}={v}" for k, v in parts.items())


def drift_response(score: float, threshold: float) -> str:
    if threshold <= 0:
        raise ValueError("threshold must be positive")
    if score >= 2 * threshold:
        return "ABSTAIN"
    if score >= threshold:
        return "REDUCE_RISK"
    return "NORMAL"


def budget_guard(used: int, max_trials: int) -> int:
    if used >= max_trials:
        raise RuntimeError("exploration budget exhausted")
    return max_trials - used
