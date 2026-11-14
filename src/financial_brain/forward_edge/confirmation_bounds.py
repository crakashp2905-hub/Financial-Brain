"""Dependence-aware lower bounds for prospective alpha and after-cost utility."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from statistics import NormalDist
import math
import hashlib
import json

from .dependence import DependencePolicy, DependenceError, summarize_dependence
from .prospective_cohort import FrozenChampionPlan
from .prospective_registry import ProspectiveCohortRegistry
from .sample_size import SampleSizeError, SampleSizePlan


class ConfirmationBoundsError(ValueError):
    """Confirmation inputs do not match the frozen session schedule."""


def _sessions(values: Sequence[str]) -> tuple[str, ...]:
    try:
        parsed = tuple(date.fromisoformat(value).isoformat() for value in values)
    except (TypeError, ValueError) as exc:
        raise ConfirmationBoundsError("scheduled sessions must be ISO dates") from exc
    if not parsed or parsed != tuple(sorted(set(parsed))) or parsed != tuple(values):
        raise ConfirmationBoundsError("scheduled sessions must be nonempty, unique and ordered")
    return parsed


def _series(
    *,
    sessions: tuple[str, ...],
    values: Mapping[str, float | None],
    label: str,
    policy: DependencePolicy,
    family_test_count: int,
) -> dict:
    extra = sorted(set(values) - set(sessions))
    if extra:
        raise ConfirmationBoundsError(f"{label} contains unscheduled dates")
    missing = tuple(day for day in sessions if day not in values or values[day] is None)
    if missing:
        return {
            "status": "INCOMPLETE",
            "scheduled_sessions": len(sessions),
            "observed_sessions": len(sessions) - len(missing),
            "missing_sessions": list(missing),
            "point_estimate": None,
            "lower_confidence_bound": None,
            "confidence_level": 1 - policy.alpha / family_test_count,
            "familywise_alpha": policy.alpha,
            "metric_family_size": family_test_count,
            "per_metric_alpha": policy.alpha / family_test_count,
            "policy_id": policy.policy_id,
            "policy_sha256": policy.sha256(),
        }
    sample = []
    for day in sessions:
        value = values[day]
        if value is None:
            raise ConfirmationBoundsError(f"{label} unexpectedly contains a missing value")
        sample.append(float(value))
    if not all(math.isfinite(x) for x in sample):
        raise ConfirmationBoundsError(f"{label} values must be finite")
    if len(sample) < 3 or policy.hac_lags >= len(sample):
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "scheduled_sessions": len(sessions),
            "observed_sessions": len(sample),
            "missing_sessions": [],
            "point_estimate": sum(sample) / len(sample) if sample else None,
            "lower_confidence_bound": None,
            "confidence_level": 1 - policy.alpha / family_test_count,
            "familywise_alpha": policy.alpha,
            "metric_family_size": family_test_count,
            "per_metric_alpha": policy.alpha / family_test_count,
            "policy_id": policy.policy_id,
            "policy_sha256": policy.sha256(),
        }
    try:
        result = summarize_dependence(sample, policy)
    except DependenceError as exc:
        raise ConfirmationBoundsError(f"invalid frozen dependence policy or sample: {exc}") from exc
    se = float(result["newey_west_standard_error"])
    mean = float(result["mean_return"])
    # A zero estimated standard error is degenerate evidence, not a zero-width proof.
    per_metric_alpha = policy.alpha / family_test_count
    lcb = mean - NormalDist().inv_cdf(1 - per_metric_alpha) * se if se > 0 else None
    confirmed = mean > 0 and lcb is not None and lcb > 0
    return {
        "status": "CONFIRMED" if confirmed else ("UNDEFINED_VARIANCE" if lcb is None else "NOT_CONFIRMED"),
        "scheduled_sessions": len(sessions),
        "observed_sessions": len(sample),
        "missing_sessions": [],
        "point_estimate": mean,
        "lower_confidence_bound": lcb,
        "newey_west_standard_error": se,
        "effective_sample_size": result["effective_sample_size"],
        "confidence_level": 1 - per_metric_alpha,
        "familywise_alpha": policy.alpha,
        "metric_family_size": family_test_count,
        "per_metric_alpha": per_metric_alpha,
        "bound_method": "one-sided normal lower bound using frozen Newey-West standard error with Bonferroni allocation across both metrics",
        "policy_id": policy.policy_id,
        "policy_sha256": policy.sha256(),
    }


def confirmation_bounds(
    *,
    scheduled_sessions: Sequence[str],
    daily_net_alpha: Mapping[str, float | None],
    after_cost_portfolio_utility: Mapping[str, float | None],
    policy: DependencePolicy,
    planned_looks: int = 1,
) -> dict:
    """Report bounds on all scheduled observations and confirm only when both clear zero.

    Alpha and portfolio utility retain their own units. Utility values must already
    include transaction costs and risk penalties under the frozen portfolio policy.
    """
    sessions = _sessions(scheduled_sessions)
    if type(planned_looks) is not int or planned_looks < 1:
        raise ConfirmationBoundsError("planned looks must be a positive integer")
    family_test_count = 2 * planned_looks
    alpha = _series(sessions=sessions, values=daily_net_alpha, label="daily_net_alpha",
                    policy=policy, family_test_count=family_test_count)
    utility = _series(sessions=sessions, values=after_cost_portfolio_utility,
                      label="after_cost_portfolio_utility", policy=policy,
                      family_test_count=family_test_count)
    confirmed = alpha["status"] == "CONFIRMED" and utility["status"] == "CONFIRMED"
    return {
        "status": "CONFIRMED" if confirmed else "NOT_CONFIRMED",
        "scheduled_denominator": len(sessions),
        "familywise_alpha": policy.alpha,
        "metric_family_size": family_test_count,
        "planned_looks": planned_looks,
        "per_metric_alpha": policy.alpha / family_test_count,
        "daily_net_alpha": alpha,
        "after_cost_portfolio_utility": utility,
        "promotion_label_rule": "CONFIRMED requires a positive point estimate and strictly positive lower confidence bound for both measures on the complete scheduled sample",
    }


def confirmation_session_outcome_sha256(
    *, session: str, daily_net_alpha: Mapping[str, float | None],
    after_cost_portfolio_utility: Mapping[str, float | None],
) -> str:
    """Digest both confirmation metrics for one session, retaining missing values explicitly."""
    values: list[float | None] = []
    observed = False
    for series in (daily_net_alpha, after_cost_portfolio_utility):
        value = series.get(session)
        if value is not None:
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ConfirmationBoundsError("confirmation values must be finite")
            observed = True
            values.append(numeric)
        else:
            values.append(None)
    if not observed:
        raise ConfirmationBoundsError("cannot register a session with no observed confirmation metric")
    payload = {"session": session, "daily_net_alpha": values[0],
               "after_cost_portfolio_utility": values[1]}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def registered_confirmation_bounds(
    *,
    cohort_registry: ProspectiveCohortRegistry,
    cohort_id: str,
    daily_net_alpha: Mapping[str, float | None],
    after_cost_portfolio_utility: Mapping[str, float | None],
    policy: DependencePolicy,
    sample_size_plan: SampleSizePlan,
) -> dict:
    """Verify cohort receipts and powered sample design before confirming both metrics."""
    try:
        cohort: FrozenChampionPlan = cohort_registry.resolve(cohort_id)
        receipts = cohort_registry.observations(cohort_id)
    except (TypeError, ValueError) as exc:
        raise ConfirmationBoundsError("registered prospective cohort could not be resolved") from exc
    try:
        derived_plan = SampleSizePlan.from_dict(sample_size_plan.as_dict())
    except (AttributeError, SampleSizeError) as exc:
        raise ConfirmationBoundsError("a validated derived sample-size plan is required") from exc
    sessions = _sessions(cohort.confirmation_sessions)
    if derived_plan != sample_size_plan:
        raise ConfirmationBoundsError("sample-size plan differs from its derived requirements")
    if (derived_plan.familywise_alpha != policy.alpha
            or derived_plan.frozen_at_utc != cohort.frozen_at_utc
            or derived_plan.first_observation_at_utc != cohort.first_confirmation_observation_at_utc
            or policy.frozen_at_utc != cohort.frozen_at_utc
            or policy.first_observation_at_utc != cohort.first_confirmation_observation_at_utc):
        raise ConfirmationBoundsError("sample and dependence policies differ from the registered cohort")
    report = confirmation_bounds(
        scheduled_sessions=sessions,
        daily_net_alpha=daily_net_alpha,
        after_cost_portfolio_utility=after_cost_portfolio_utility,
        policy=policy,
        planned_looks=derived_plan.planned_looks,
    )
    digests = {}
    for session in sessions:
        has_data = (daily_net_alpha.get(session) is not None
                    or after_cost_portfolio_utility.get(session) is not None)
        if has_data:
            digests[session] = confirmation_session_outcome_sha256(
                session=session, daily_net_alpha=daily_net_alpha,
                after_cost_portfolio_utility=after_cost_portfolio_utility,
            )
    receipt_digests = {row["session"]: row["outcome_sha256"] for row in receipts}
    if receipt_digests != digests:
        raise ConfirmationBoundsError(
            "confirmation metric values do not match the durable cohort session receipts")
    if len(sessions) < derived_plan.required_sessions:
        report["status"] = "SAMPLE_SIZE_NOT_MET"
        for metric in ("daily_net_alpha", "after_cost_portfolio_utility"):
            item = report[metric]
            item["status"] = "SAMPLE_SIZE_NOT_MET"
            item["lower_confidence_bound"] = None
    report["cohort_id"] = cohort_id
    report["cohort_plan_sha256"] = cohort.plan_sha256
    report["sample_size_plan_sha256"] = derived_plan.sha256()
    report["required_sessions"] = derived_plan.required_sessions
    report["observed_sessions"] = len(receipt_digests)
    return report
