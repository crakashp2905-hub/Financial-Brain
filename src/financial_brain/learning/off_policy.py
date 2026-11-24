"""Support-checked contextual-bandit off-policy evaluation primitives."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping


class OffPolicyError(ValueError):
    """Malformed logged decision or invalid OPE configuration."""


DEFAULT_MINIMUM_EFFECTIVE_SAMPLE_SIZE = 30.0


@dataclass(frozen=True, slots=True)
class LoggedBanditDecision:
    decision_id: str
    logged_action: str
    reward: float
    target_probabilities: Mapping[str, float]
    behavior_probabilities: Mapping[str, float] | None
    action_values: Mapping[str, float] | None = None

    def __post_init__(self) -> None:
        if not self.decision_id.strip() or not self.logged_action.strip():
            raise OffPolicyError("decision ID and logged action are required")
        if not math.isfinite(self.reward):
            raise OffPolicyError("logged reward must be finite")
        _probabilities(self.target_probabilities, "target")
        if self.logged_action not in self.target_probabilities:
            raise OffPolicyError("logged action is absent from the target action space")
        if self.behavior_probabilities is not None:
            _probabilities(self.behavior_probabilities, "behavior")
            if set(self.behavior_probabilities) != set(self.target_probabilities):
                raise OffPolicyError("target and behavior must define the same explicit action set")
        if self.action_values is not None:
            if set(self.action_values) != set(self.target_probabilities) or any(
                not math.isfinite(value) for value in self.action_values.values()
            ):
                raise OffPolicyError("action values must be finite and cover the full action set")


def evaluate_logged_policy(
    decisions: tuple[LoggedBanditDecision, ...],
    *,
    variant_id: str,
    max_importance_weight: float | None = None,
    minimum_effective_sample_size: float = DEFAULT_MINIMUM_EFFECTIVE_SAMPLE_SIZE,
) -> dict[str, object]:
    """Evaluate a target policy only on explicit, positivity-supported logs.

    Computes direct-method, IPS, self-normalized IPS and doubly robust estimates
    when their required action-value inputs exist. Propensity clipping is an
    explicitly named variant. Confidence intervals and sequential trajectory
    OPE are outside this contextual one-step contract.
    """
    if not variant_id.strip():
        raise OffPolicyError("a predeclared variant ID is required")
    if max_importance_weight is not None and (
        not math.isfinite(max_importance_weight) or max_importance_weight <= 0
    ):
        raise OffPolicyError("maximum importance weight must be finite and positive")
    if not math.isfinite(minimum_effective_sample_size) or minimum_effective_sample_size < 1:
        raise OffPolicyError("minimum effective sample size must be finite and at least one")
    if not decisions:
        return _no_ope(variant_id, 0, "NO_LOGGED_DECISIONS")
    if len({row.decision_id for row in decisions}) != len(decisions):
        raise OffPolicyError("decision IDs must be unique")
    if any(row.behavior_probabilities is None for row in decisions):
        return _no_ope(variant_id, len(decisions), "BEHAVIOR_PROPENSITY_MISSING")

    raw_weights: list[float] = []
    rewards: list[float] = []
    direct_values: list[float] = []
    dr_residuals: list[float] = []
    has_values = all(row.action_values is not None for row in decisions)
    for row in decisions:
        behavior = row.behavior_probabilities
        assert behavior is not None
        for action, target_probability in row.target_probabilities.items():
            if target_probability > 0 and behavior[action] <= 0:
                return _no_ope(variant_id, len(decisions), "TARGET_ACTION_OUTSIDE_BEHAVIOR_SUPPORT")
        logged_behavior = behavior[row.logged_action]
        if logged_behavior <= 0:
            return _no_ope(variant_id, len(decisions), "LOGGED_ACTION_HAS_ZERO_BEHAVIOR_PROBABILITY")
        weight = row.target_probabilities[row.logged_action] / logged_behavior
        raw_weights.append(weight)
        rewards.append(row.reward)
        if has_values:
            values = row.action_values
            assert values is not None
            direct = sum(row.target_probabilities[action] * values[action]
                         for action in row.target_probabilities)
            direct_values.append(direct)
            dr_residuals.append(row.reward - values[row.logged_action])

    weights = [min(weight, max_importance_weight) if max_importance_weight is not None else weight
               for weight in raw_weights]
    sum_weights = sum(weights)
    sum_squares = sum(weight * weight for weight in weights)
    effective_n = sum_weights * sum_weights / sum_squares if sum_squares > 0 else 0.0
    diagnostics = {
        "n": len(decisions),
        "effective_sample_size": effective_n,
        "max_raw_importance_weight": max(raw_weights, default=0.0),
        "max_used_importance_weight": max(weights, default=0.0),
        "clipped_weight_count": sum(1 for raw, used in zip(raw_weights, weights, strict=True) if used < raw),
        "target_support_passed": True,
        "variant_id": variant_id,
        "max_importance_weight": max_importance_weight,
    }
    if sum_weights <= 0 or effective_n < minimum_effective_sample_size:
        return {"status": "NO_VALID_OPE", "reason": "INSUFFICIENT_WEIGHTED_SUPPORT", **diagnostics,
                "estimates": {}}

    ips = sum(weight * reward for weight, reward in zip(weights, rewards, strict=True)) / len(decisions)
    snips = sum(weight * reward for weight, reward in zip(weights, rewards, strict=True)) / sum_weights
    estimates: dict[str, float | None] = {
        "ips": ips,
        "self_normalized_ips": snips,
        "direct_method": (sum(direct_values) / len(decisions)) if has_values else None,
        "doubly_robust": (
            sum(direct + weight * residual for direct, weight, residual in
                zip(direct_values, weights, dr_residuals, strict=True)) / len(decisions)
            if has_values else None
        ),
    }
    return {"status": "OPE_ESTIMATE", "reason": None, **diagnostics, "estimates": estimates,
            "confidence_interval": None,
            "limitation": "Contextual one-step estimates only; clustered uncertainty and sequential OPE are not supplied."}


def _probabilities(values: Mapping[str, float], label: str) -> None:
    if not values or any(not key.strip() for key in values):
        raise OffPolicyError(f"{label} probabilities must cover named actions")
    if any(not math.isfinite(value) or value < 0 or value > 1 for value in values.values()):
        raise OffPolicyError(f"{label} probabilities must be finite and in [0, 1]")
    if not math.isclose(sum(values.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise OffPolicyError(f"{label} probabilities must sum to one")


def _no_ope(variant_id: str, count: int, reason: str) -> dict[str, object]:
    return {"status": "NO_VALID_OPE", "reason": reason, "n": count, "variant_id": variant_id,
            "target_support_passed": False, "estimates": {}}


__all__ = ["LoggedBanditDecision", "OffPolicyError", "evaluate_logged_policy"]
