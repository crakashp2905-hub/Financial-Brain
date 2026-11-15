"""Pre-registered regime and execution-stratum outcome summaries."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from statistics import NormalDist
import math

from .evidence_stats import StatsError, newey_west_se
from .prospective_cohort import FrozenChampionPlan
from .prospective_registry import ProspectiveCohortRegistry


class StratificationError(ValueError):
    """Invalid cohort outcomes or stratum registry."""


DIMENSIONS = ("volatility", "market_regime", "liquidity", "market_cap", "transaction_cost")


@dataclass(frozen=True)
class StratificationPlan:
    """Frozen, content-addressed subgroup design bound to a confirmation cohort."""

    cohort_plan_sha256: str
    scheduled_sessions: tuple[str, ...]
    registered_strata: tuple[tuple[str, tuple[str, ...]], ...]
    hac_lags: int
    familywise_alpha: float
    aggregation_policy_sha256: str
    frozen_at_utc: str
    first_observation_at_utc: str
    plan_sha256: str

    def as_dict(self) -> dict:
        return asdict(self)

    def sha256(self) -> str:
        return _plan_digest(self)


def _plan_payload(plan: StratificationPlan) -> dict:
    value = asdict(plan)
    value.pop("plan_sha256")
    return value


def _plan_digest(plan: StratificationPlan) -> str:
    canonical = json.dumps(_plan_payload(plan), sort_keys=True, separators=(",", ":"),
                           allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _aware_utc(value: str, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise StratificationError(f"{label} must be an ISO timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StratificationError(f"{label} must be an ISO timezone-aware timestamp")
    return parsed.astimezone(timezone.utc).isoformat()


def _validate_cohort(cohort: FrozenChampionPlan) -> None:
    development = _sessions(cohort.development_sessions)
    confirmation = _sessions(cohort.confirmation_sessions)
    if set(development) & set(confirmation) or confirmation[0] <= development[-1]:
        raise StratificationError("cohort development and confirmation schedules are invalid")
    if (not isinstance(cohort.champion_id, str) or not cohort.champion_id.strip()
            or any(len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
                   for value in (cohort.development_returns_sha256, cohort.plan_sha256))):
        raise StratificationError("cohort identity digests are invalid")
    frozen = _aware_utc(cohort.frozen_at_utc, "cohort frozen time")
    first = _aware_utc(cohort.first_confirmation_observation_at_utc,
                       "first confirmation observation time")
    if datetime.fromisoformat(frozen) >= datetime.fromisoformat(first):
        raise StratificationError("cohort must be frozen before its first observation")
    payload = {
        "champion_id": cohort.champion_id,
        "development_sessions": development,
        "confirmation_sessions": confirmation,
        "development_returns_sha256": cohort.development_returns_sha256,
        "frozen_at_utc": cohort.frozen_at_utc,
        "first_confirmation_observation_at_utc": cohort.first_confirmation_observation_at_utc,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != cohort.plan_sha256:
        raise StratificationError("frozen cohort digest is invalid")


def freeze_stratification_plan(
    *,
    cohort: FrozenChampionPlan,
    registered_strata: Mapping[str, Sequence[str]],
    hac_lags: int,
    familywise_alpha: float,
    aggregation_policy_sha256: str,
) -> StratificationPlan:
    """Freeze strata and their aggregation-policy identity before cohort outcomes exist."""
    if not isinstance(cohort, FrozenChampionPlan):
        raise StratificationError("stratification plan requires a frozen confirmation cohort")
    _validate_cohort(cohort)
    sessions = _sessions(cohort.confirmation_sessions)
    strata = _registered_strata(registered_strata)
    if type(hac_lags) is not int or not 0 <= hac_lags < len(sessions):
        raise StratificationError("HAC lags must be nonnegative and smaller than the schedule")
    if (isinstance(familywise_alpha, bool) or not isinstance(familywise_alpha, (int, float))
            or not math.isfinite(familywise_alpha) or not 0 < familywise_alpha < 1):
        raise StratificationError("familywise alpha must be between zero and one")
    if (not isinstance(aggregation_policy_sha256, str)
            or len(aggregation_policy_sha256) != 64
            or any(char not in "0123456789abcdef" for char in aggregation_policy_sha256)):
        raise StratificationError("aggregation policy must have a lowercase SHA-256 digest")
    frozen = _aware_utc(cohort.frozen_at_utc, "cohort frozen time")
    first = _aware_utc(cohort.first_confirmation_observation_at_utc,
                       "first confirmation observation time")
    if datetime.fromisoformat(frozen) >= datetime.fromisoformat(first):
        raise StratificationError("cohort must be frozen before its first observation")
    frozen_strata = tuple((key, strata[key]) for key in DIMENSIONS)
    payload = {
        "cohort_plan_sha256": cohort.plan_sha256,
        "scheduled_sessions": sessions,
        "registered_strata": frozen_strata,
        "hac_lags": hac_lags,
        "familywise_alpha": float(familywise_alpha),
        "aggregation_policy_sha256": aggregation_policy_sha256,
        "frozen_at_utc": frozen,
        "first_observation_at_utc": first,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return StratificationPlan(
        cohort_plan_sha256=cohort.plan_sha256,
        scheduled_sessions=sessions,
        registered_strata=frozen_strata,
        hac_lags=hac_lags,
        familywise_alpha=float(familywise_alpha),
        aggregation_policy_sha256=aggregation_policy_sha256,
        frozen_at_utc=frozen,
        first_observation_at_utc=first,
        plan_sha256=digest,
    )


def stratify_frozen_outcomes(
    *,
    plan: StratificationPlan,
    cohort: FrozenChampionPlan,
    observations: Sequence[Mapping[str, object]],
) -> dict:
    """Evaluate outcomes only under the frozen cohort-bound subgroup design."""
    if not isinstance(plan, StratificationPlan) or plan.sha256() != plan.plan_sha256:
        raise StratificationError("stratification plan digest is invalid")
    if not isinstance(cohort, FrozenChampionPlan):
        raise StratificationError("evaluation requires the frozen confirmation cohort")
    _validate_cohort(cohort)
    if (plan.cohort_plan_sha256 != cohort.plan_sha256
            or plan.scheduled_sessions != tuple(cohort.confirmation_sessions)
            or plan.frozen_at_utc != cohort.frozen_at_utc
            or plan.first_observation_at_utc != cohort.first_confirmation_observation_at_utc):
        raise StratificationError("stratification plan differs from its frozen cohort")
    report = stratify_outcomes(
        scheduled_sessions=plan.scheduled_sessions,
        registered_strata=dict(plan.registered_strata),
        observations=observations,
        hac_lags=plan.hac_lags,
        familywise_alpha=plan.familywise_alpha,
    )
    return {**report, "stratification_plan_sha256": plan.plan_sha256,
            "cohort_plan_sha256": plan.cohort_plan_sha256,
            "aggregation_policy_sha256": plan.aggregation_policy_sha256}


def stratify_registered_outcomes(
    *,
    plan: StratificationPlan,
    cohort_registry: ProspectiveCohortRegistry,
    cohort_id: str,
    observations: Sequence[Mapping[str, object]],
) -> dict:
    """Resolve the cohort from its durable registry before applying the frozen strata."""
    try:
        cohort = cohort_registry.resolve(cohort_id)
        receipts = cohort_registry.observations(cohort_id)
    except (TypeError, ValueError) as exc:
        raise StratificationError("registered prospective cohort could not be resolved") from exc
    report = stratify_frozen_outcomes(plan=plan, cohort=cohort, observations=observations)
    digests: dict[str, str] = {}
    for session in {str(row.get("session", "")) for row in observations}:
        digests[session] = stratification_session_outcome_sha256(observations, session)
    receipt_digests = {row["session"]: row["outcome_sha256"] for row in receipts}
    if receipt_digests != digests:
        raise StratificationError(
            "stratified outcome rows do not match the durable cohort observation receipts")
    return {**report, "registered_observed_sessions": len(receipts)}


def stratification_session_outcome_sha256(
    observations: Sequence[Mapping[str, object]], session: str
) -> str:
    """Digest the canonical per-session rows used by the registered stratification report."""
    rows = []
    for row in observations:
        if row.get("session") != session:
            continue
        try:
            item = {
                "dimension": row["dimension"],
                "stratum": row["stratum"],
                "session": row["session"],
                "net_alpha": float(row["net_alpha"]),  # type: ignore[arg-type]
                "after_cost_utility": float(row["after_cost_utility"]),  # type: ignore[arg-type]
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise StratificationError("cannot digest malformed stratified outcome rows") from exc
        rows.append(item)
    if not rows:
        raise StratificationError("cannot digest an empty session outcome")
    rows.sort(key=lambda row: (str(row["dimension"]), str(row["stratum"])))
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
            alpha = float(row["net_alpha"])  # type: ignore[arg-type]
            utility = float(row["after_cost_utility"])  # type: ignore[arg-type]
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
                    observed_mean = sum(sample) / len(sample)
                    lower, upper = observed_mean - critical * se, observed_mean + critical * se
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
