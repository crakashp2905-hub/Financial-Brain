"""Analytics on formations and outcomes: trend context, volume state, metrics, audits and models.

Every function here reads only data it is given and never fetches anything. Models are
descriptive and labelled uncalibrated until matured labels exist (CP-0286, CP-0291).
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import Any

from financial_brain.patterns.detectors import Formation

# Recognition delay in bars after the last observed bar, per source (CP-0036).
RECOGNITION_DELAY_BARS = {
    "pivot": "right_bars",  # enforced by causal_pivots: known_at = index + right
    "completed_candle": 0,  # decided at the bar close
    "weekly": "week_close",  # only a FINAL week may be used (candles.final_weeks)
    "market_close": 0,
}


def trend_context(closes: Sequence[float], t: int, lookback: int) -> dict[str, float | str]:
    """Past-only trend: return, fitted slope and higher-high/higher-low count (CP-0064)."""
    if lookback < 2 or t - lookback + 1 < 0 or t >= len(closes):
        raise ValueError("window must lie inside the observed bars")
    window = list(closes[t - lookback + 1 : t + 1])
    if any(not math.isfinite(x) or x <= 0 for x in window):
        raise ValueError("prices must be finite and positive")
    ret = window[-1] / window[0] - 1.0
    n = len(window)
    xs = range(n)
    mx = (n - 1) / 2
    my = sum(window) / n
    denom = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, window, strict=True)) / denom
    ups = sum(1 for a, b in zip(window, window[1:], strict=False) if b > a)
    label = "UP" if ret > 0 and slope > 0 else "DOWN" if ret < 0 and slope < 0 else "MIXED"
    return {"return": ret, "slope": slope, "up_steps": float(ups), "label": label}


def volume_contraction(volumes: Sequence[float | None], t: int, window: int) -> float | None:
    """Recent average volume relative to the prior window; unknown volume gives None, never zero (CP-0065)."""
    if window < 1 or t - 2 * window + 1 < 0:
        return None
    recent = [v for v in volumes[t - window + 1 : t + 1]]
    prior = [v for v in volumes[t - 2 * window + 1 : t - window + 1]]
    if any(v is None or v < 0 for v in recent + prior):
        return None
    recent_vals = [float(v) for v in recent if v is not None]
    prior_vals = [float(v) for v in prior if v is not None]
    prior_mean = sum(prior_vals) / window
    if prior_mean == 0:
        return None
    return sum(recent_vals) / window / prior_mean


def observation_side(direction: str, *, has_long_position: bool) -> str:
    """A bearish observation can only mean exit for an existing long, never a short entry (CP-0032)."""
    if direction == "bullish":
        return "LONG_CANDIDATE"
    if direction == "bearish":
        return "EXIT_ONLY" if has_long_position else "NO_ACTION"
    return "NO_ACTION"


def reference_levels(formation: Formation) -> dict[str, Any]:
    """Descriptive reference levels, always labelled and never projected as targets (CP-0225)."""
    levels = {k: v for k, v in formation.geometry.items() if k in ("top", "bottom", "neckline")}
    return {"label": "DESCRIPTIVE_REFERENCE", "levels": levels, "projected_target": None}


def time_to_confirm(formations: list[Formation]) -> list[int]:
    """Bars from candidate knowledge to confirmation, for confirmed formations only (CP-0130)."""
    return sorted(f.confirmed_index - f.candidate_known_index for f in formations if f.confirmed_index is not None)


def lifecycle_counts(formations: list[Formation], *, now: int) -> dict[str, int]:
    """Counts of CANDIDATE, CONFIRMED and FAILED as known at ``now`` (CP-0130, CP-0235)."""
    counts = {"CANDIDATE": 0, "CONFIRMED": 0, "FAILED": 0}
    for f in formations:
        state = f.state_at(now)
        if state in counts:
            counts[state] += 1
    return counts


def daily_summary(formations: list[Formation], *, day: int, no_trade_count: int) -> dict[str, Any]:
    """New shapes, confirmations, failures and no-trade events for one bar, with provenance labels (CP-0246)."""
    return {
        "day_index": day,
        "new_shapes": sum(1 for f in formations if f.candidate_known_index == day),
        "confirmations": sum(1 for f in formations if f.confirmed_index == day),
        "failures": sum(1 for f in formations if f.failed_index == day),
        "no_trade_events": no_trade_count,
        "labels": ["DESCRIPTIVE", "RESEARCH ONLY"],
    }


def export_formations_json(formations: list[Formation]) -> str:
    """Deterministic JSON export (CP-0134). Parquet and SQL views are not built."""
    rows = [
        {
            "detector": f.detector,
            "version": f.version,
            "start_index": f.start_index,
            "candidate_known_index": f.candidate_known_index,
            "confirmed_index": f.confirmed_index,
            "confirmed_direction": f.confirmed_direction,
            "failed_index": f.failed_index,
            "geometry": f.geometry,
        }
        for f in formations
    ]
    return json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def version_count_audit(old: dict[str, int], new: dict[str, int]) -> dict[str, dict[str, int]]:
    """Candidate-count changes between detector versions, so history is never silently rewritten (CP-0135)."""
    keys = sorted(set(old) | set(new))
    return {k: {"old": old.get(k, 0), "new": new.get(k, 0), "delta": new.get(k, 0) - old.get(k, 0)} for k in keys}


def missing_outcomes(outcomes: dict[str, float | None], *, policy: str) -> dict[str, Any]:
    """Missing outcomes are counted and treated by an explicit policy, never dropped silently (CP-0164)."""
    if policy not in ("count_as_zero", "exclude_and_report"):
        raise ValueError("policy must be count_as_zero or exclude_and_report")
    missing = [k for k, v in outcomes.items() if v is None]
    present = [v for v in outcomes.values() if v is not None]
    values = present + ([0.0] * len(missing) if policy == "count_as_zero" else [])
    mean = sum(values) / len(values) if values else 0.0
    return {"n_total": len(outcomes), "n_missing": len(missing), "mean": mean, "policy": policy}


BENCHMARK_SPEC: dict[str, Any] = {
    "name": "frozen_benchmarks_v1",
    "benchmarks": ["cash", "naive_momentum_12_1", "52_week_high_proximity", "low_volatility"],
    "common_dates": "identical for all comparisons",
    "costs_bps": 12.0,
    "universe": "point-in-time eligible names only",
}


def benchmark_spec_sha256() -> str:
    from financial_brain.patterns.spec import canonical_json, sha256_hex

    return sha256_hex(canonical_json(BENCHMARK_SPEC))


def overlap_leaks(*, embargo: int, label_horizon: int) -> bool:
    """True when the embargo is shorter than the label horizon, so training labels overlap evaluation (CP-0284)."""
    if embargo < 0 or label_horizon < 0:
        raise ValueError("embargo and horizon must be non-negative")
    return embargo < label_horizon


def fit_completion_model(labels: list[tuple[str, int]]) -> dict[str, Any]:
    """Descriptive completion model: confirmed fraction per detector from matured labels (CP-0286).

    ``labels`` holds (detector, 1 if confirmed else 0) for matured candidates only. The output is
    not calibrated and must never be presented as a probability of profit.
    """
    counts: dict[str, list[int]] = {}
    for detector, confirmed in labels:
        bucket = counts.setdefault(detector, [0, 0])
        bucket[0] += 1
        bucket[1] += confirmed
    if not labels:
        return {"status": "NO_CALIBRATION", "n": 0, "rates": {}}
    rates = {k: (v[1] + 1) / (v[0] + 2) for k, v in sorted(counts.items())}  # Laplace-smoothed
    return {"status": "UNCALIBRATED_DESCRIPTIVE", "n": len(labels), "rates": rates}


def model_bytes(model: dict[str, Any]) -> bytes:
    return json.dumps(model, sort_keys=True, separators=(",", ":")).encode("utf-8")
