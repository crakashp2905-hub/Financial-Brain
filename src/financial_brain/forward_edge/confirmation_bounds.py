"""Dependence-aware lower bounds for prospective alpha and after-cost utility."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from statistics import NormalDist
import math

from .dependence import DependencePolicy, DependenceError, summarize_dependence


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
            "confidence_level": 1 - policy.alpha,
            "policy_id": policy.policy_id,
            "policy_sha256": policy.sha256(),
        }
    sample = [float(values[day]) for day in sessions]
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
            "confidence_level": 1 - policy.alpha,
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
    lcb = mean - NormalDist().inv_cdf(1 - policy.alpha) * se if se > 0 else None
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
        "confidence_level": 1 - policy.alpha,
        "bound_method": "one-sided normal lower bound using frozen Newey-West standard error",
        "policy_id": policy.policy_id,
        "policy_sha256": policy.sha256(),
    }


def confirmation_bounds(
    *,
    scheduled_sessions: Sequence[str],
    daily_net_alpha: Mapping[str, float | None],
    after_cost_portfolio_utility: Mapping[str, float | None],
    policy: DependencePolicy,
) -> dict:
    """Report bounds on all scheduled observations and confirm only when both clear zero.

    Alpha and portfolio utility retain their own units. Utility values must already
    include transaction costs and risk penalties under the frozen portfolio policy.
    """
    sessions = _sessions(scheduled_sessions)
    alpha = _series(sessions=sessions, values=daily_net_alpha, label="daily_net_alpha", policy=policy)
    utility = _series(sessions=sessions, values=after_cost_portfolio_utility,
                      label="after_cost_portfolio_utility", policy=policy)
    confirmed = alpha["status"] == "CONFIRMED" and utility["status"] == "CONFIRMED"
    return {
        "status": "CONFIRMED" if confirmed else "NOT_CONFIRMED",
        "scheduled_denominator": len(sessions),
        "daily_net_alpha": alpha,
        "after_cost_portfolio_utility": utility,
        "promotion_label_rule": "CONFIRMED requires a positive point estimate and strictly positive lower confidence bound for both measures on the complete scheduled sample",
    }
