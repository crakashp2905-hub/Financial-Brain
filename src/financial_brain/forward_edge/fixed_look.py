"""Pre-registered efficacy looks with anytime risk-harm stopping."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math


class FixedLookError(ValueError):
    """A fixed-look design or interim observation is invalid."""


def _utc(value: str, label: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise FixedLookError(f"{label} must be a timezone-aware ISO timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise FixedLookError(f"{label} must be a timezone-aware ISO timestamp")
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class FixedLookPolicy:
    policy_id: str
    checkpoints: tuple[int, ...]
    familywise_alpha: float
    max_drawdown_floor: float
    frozen_at_utc: str
    first_observation_at_utc: str

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise FixedLookError("policy id is required")
        if not self.checkpoints or tuple(sorted(set(self.checkpoints))) != self.checkpoints:
            raise FixedLookError("checkpoints must be nonempty, unique and increasing")
        if any(type(value) is not int or value < 1 for value in self.checkpoints):
            raise FixedLookError("checkpoints must be positive integer session counts")
        if not math.isfinite(self.familywise_alpha) or not 0 < self.familywise_alpha < 1:
            raise FixedLookError("familywise alpha must be between zero and one")
        if not math.isfinite(self.max_drawdown_floor) or not -1 < self.max_drawdown_floor < 0:
            raise FixedLookError("max drawdown floor must be a finite loss fraction in (-1, 0)")
        frozen = _utc(self.frozen_at_utc, "frozen_at_utc")
        first = _utc(self.first_observation_at_utc, "first_observation_at_utc")
        if frozen >= first:
            raise FixedLookError("look schedule must be frozen before the first observation")
        object.__setattr__(self, "frozen_at_utc", frozen.isoformat())
        object.__setattr__(self, "first_observation_at_utc", first.isoformat())

    @property
    def per_look_alpha(self) -> float:
        return self.familywise_alpha / len(self.checkpoints)

    def sha256(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"),
                             allow_nan=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluate_look(
    policy: FixedLookPolicy,
    *,
    completed_sessions: int,
    lower_confidence_bound: float | None,
    observed_max_drawdown: float | None,
) -> dict:
    """Allow efficacy decisions only at registered looks; stop for harm at any time."""
    if type(completed_sessions) is not int or completed_sessions < 1:
        raise FixedLookError("completed sessions must be a positive integer")
    if lower_confidence_bound is not None and (
        isinstance(lower_confidence_bound, bool) or not math.isfinite(lower_confidence_bound)
    ):
        raise FixedLookError("lower confidence bound must be finite")
    if observed_max_drawdown is not None:
        if (isinstance(observed_max_drawdown, bool) or not math.isfinite(observed_max_drawdown)
                or observed_max_drawdown > 0):
            raise FixedLookError("observed maximum drawdown must be a finite nonpositive fraction")
        if observed_max_drawdown <= policy.max_drawdown_floor:
            return {
                "status": "STOP_FOR_RISK_HARM",
                "orders_permission": "DISABLED_FOR_RISK_HARM",
                "efficacy_decision": "WITHHELD",
                "completed_sessions": completed_sessions,
                "registered_look": completed_sessions in policy.checkpoints,
                "policy_id": policy.policy_id,
                "policy_sha256": policy.sha256(),
            }
    if observed_max_drawdown is None:
        return {
            "status": "RISK_EVIDENCE_MISSING",
            "orders_permission": "NOT_AUTHORIZED",
            "efficacy_decision": "WITHHELD",
            "completed_sessions": completed_sessions,
            "registered_look": completed_sessions in policy.checkpoints,
            "policy_id": policy.policy_id,
            "policy_sha256": policy.sha256(),
        }
    if completed_sessions not in policy.checkpoints:
        return {
            "status": "UNSCHEDULED_NO_EFFICACY_DECISION",
            "orders_permission": "UNCHANGED_BY_THIS_REPORT",
            "efficacy_decision": "WITHHELD",
            "completed_sessions": completed_sessions,
            "registered_look": False,
            "policy_id": policy.policy_id,
            "policy_sha256": policy.sha256(),
        }
    if lower_confidence_bound is None or not math.isfinite(lower_confidence_bound):
        decision = "INFERENCE_MISSING"
    else:
        decision = "BOUND_POSITIVE" if lower_confidence_bound > 0 else "NOT_CONFIRMED"
    return {
        "status": "REGISTERED_LOOK",
        "orders_permission": "UNCHANGED_BY_THIS_REPORT",
        "efficacy_decision": decision,
        "completed_sessions": completed_sessions,
        "registered_look": True,
        "look_index": policy.checkpoints.index(completed_sessions) + 1,
        "per_look_alpha": policy.per_look_alpha,
        "familywise_alpha": policy.familywise_alpha,
        "lower_confidence_bound": lower_confidence_bound,
        "policy_id": policy.policy_id,
        "policy_sha256": policy.sha256(),
    }
