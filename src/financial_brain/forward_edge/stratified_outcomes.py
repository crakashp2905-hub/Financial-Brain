"""Pre-registered regime and execution-stratum outcome summaries."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from statistics import NormalDist
import math

from .evidence_stats import StatsError, newey_west_se


class StratificationError(ValueError):
    """Invalid cohort outcomes or stratum registry."""


DIMENSIONS = ("volatility", "market_regime", "liquidity", "market_cap", "transaction_cost")


def _sessions(values: Sequence[str]) -> tuple[str, ...]:
    try:
        parsed = tuple(date.fromisoformat(item).isoformat() for item in values)
    except (TypeError, ValueError) as exc:
        raise StratificationError("scheduled sessions must be ISO dates") from exc
    if not parsed or parsed != tuple(sorted(set(parsed))) or parsed != tuple(values):
        raise StratificationError("scheduled sessions must be nonempty, unique and ordered")
    return parsed


def _registered_strata(values: Mapping[str, Sequence[str]]) -> dict[str, tuple[str, ...]]:
    if set(values) != set(DIMENSIONS):
        raise StratificationError("registry must define all five required dimensions")
    out: dict[str, tuple[str, ...]] = {}
    for dimension in DIMENSIONS:
        if isinstance(values[dimension], str):
            raise StratificationError(f"{dimension} strata must be a sequence of labels")
        labels = tuple(values[dimension])
        if not labels or any(not isinstance(label, str) or not label.strip() for label in labels):
            raise StratificationError(f"{dimension} strata must have nonempty labels")
        if len(set(labels)) != len(labels):
            raise StratificationError(f"{dimension} strata must be unique")
        out[dimension] = tuple(sorted(labels))
    return out


def stratify_outcomes(
    *,
    scheduled_sessions: Sequence[str],
    registered_strata: Mapping[str, Sequence[str]],
    observations: Sequence[Mapping[str, object]],
    hac_lags: int,
    familywise_alpha: float = 0.05,
) -> dict:
    """Summarize each frozen bucket with simultaneous Newey-West uncertainty bands.

    Each observation is one already-aggregated outcome per (dimension, bucket, session).
    Callers must predefine those aggregation weights and include cash/no-trade outcomes.
    """
    sessions = _sessions(scheduled_sessions)
    strata = _registered_strata(registered_strata)
    if type(hac_lags) is not int or hac_lags < 0:
        raise StratificationError("HAC lags must be a nonnegative integer")
    if not math.isfinite(familywise_alpha) or not 0 < familywise_alpha < 1:
        raise StratificationError("familywise alpha must be between zero and one")
    if hac_lags >= len(sessions):
        raise StratificationError("HAC lags must be smaller than the scheduled sample")

    groups: dict[tuple[str, str], dict[str, tuple[float, float]]] = {
        (dimension, label): {} for dimension, labels in strata.items() for label in labels
    }
    for row in observations:
        try:
            dimension = str(row["dimension"])
            label = str(row["stratum"])
            session = str(row["session"])
            alpha = float(row["net_alpha"])
            utility = float(row["after_cost_utility"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StratificationError("each observation needs dimension, stratum, session and outcomes") from exc
        if dimension not in strata or label not in strata[dimension]:
            raise StratificationError("observation uses an unregistered dimension or stratum")
        if session not in sessions:
            raise StratificationError("observation contains an unscheduled session")
        if not math.isfinite(alpha) or not math.isfinite(utility):
            raise StratificationError("stratum outcomes must be finite")
        key = (dimension, label)
        if session in groups[key]:
            raise StratificationError("duplicate outcome for a registered stratum and session")
        groups[key][session] = (alpha, utility)

    # One simultaneous family covers all registered buckets and both outcome metrics.
    metric_count = 2 * len(groups)
    critical = NormalDist().inv_cdf(1 - familywise_alpha / (2 * metric_count))
    reports = []
    for (dimension, label), by_session in sorted(groups.items()):
        missing = [day for day in sessions if day not in by_session]
        report: dict[str, object] = {
            "dimension": dimension,
            "stratum": label,
            "scheduled_denominator": len(sessions),
            "observed_sessions": len(by_session),
            "missing_sessions": missing,
            "status": "INCOMPLETE" if missing else "COMPLETE",
        }
        for metric_name, tuple_index in (("daily_net_alpha", 0),
                                         ("after_cost_portfolio_utility", 1)):
            sample = [by_session[day][tuple_index] for day in sessions if day in by_session]
            mean = sum(sample) / len(sample) if sample else None
            lower = upper = None
            status = "INCOMPLETE" if missing else "INSUFFICIENT_SAMPLE"
            se = None
            if not missing and len(sample) >= 3:
                try:
                    se = newey_west_se(sample, hac_lags)
                except StatsError as exc:
                    raise StratificationError(f"cannot estimate {metric_name}: {exc}") from exc
                if se <= 0:
                    status = "UNDEFINED_VARIANCE"
                else:
                    lower, upper = mean - critical * se, mean + critical * se
                    status = "POSITIVE" if lower > 0 else (
                        "NEGATIVE" if upper < 0 else "UNCERTAIN"
                    )
            report[metric_name] = {
                "point_estimate": mean,
                "newey_west_standard_error": se,
                "simultaneous_lower_bound": lower,
                "simultaneous_upper_bound": upper,
                "status": status,
                "familywise_alpha": familywise_alpha,
                "simultaneous_metric_count": metric_count,
                "critical_value": critical,
            }
        reports.append(report)
    return {
        "status": "FRAGMENTED_OR_INCOMPLETE" if any(x["status"] != "COMPLETE" for x in reports)
        else "STRATIFIED",
        "scheduled_denominator": len(sessions),
        "registered_strata_count": len(groups),
        "simultaneous_metric_count": metric_count,
        "familywise_alpha": familywise_alpha,
        "hac_lags": hac_lags,
        "confidence_method": "simultaneous normal bands using Newey-West standard errors with Bonferroni allocation across all registered strata and both outcome metrics",
        "positive_strata_are_not_a_global_profitability_claim": True,
        "strata": reports,
    }
