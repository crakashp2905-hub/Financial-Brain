"""Immutable, provenance-bound transition rows for offline-learning datasets.

This module defines and validates data contracts only. It does not infer propensities,
create actions/fills, authorize training, or claim that replay outcomes are observed.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Any

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class TrajectoryError(ValueError):
    """Trajectory identity, timing, support or provenance is invalid."""


class PropensityStatus(StrEnum):
    KNOWN = "KNOWN"
    UNAVAILABLE = "PROPENSITY_UNAVAILABLE"


class TransitionEvidence(StrEnum):
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    DETERMINISTIC_FIXTURE = "DETERMINISTIC_FIXTURE"
    LOCAL_PAPER = "LOCAL_PAPER"
    PROSPECTIVELY_OBSERVED_SESSION = "PROSPECTIVELY_OBSERVED_SESSION"
    INDEPENDENTLY_ATTESTED_FORWARD = "INDEPENDENTLY_ATTESTED_FORWARD"


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TrajectoryError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _hash(value: str, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise TrajectoryError(f"{name} must be a lowercase SHA-256")
    return value


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        return _aware(value, "timestamp").isoformat()
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (tuple, list)):
        return [_canonical(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise TrajectoryError("state/provenance values must be finite")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TrajectoryError(f"unsupported canonical value type: {type(value).__name__}")


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class EpisodeKey:
    dataset_version: str
    instrument_id: str
    session_id: str
    strategy_id: str
    behavior_policy_version: str
    account_id: str
    episode_id: str

    def __post_init__(self) -> None:
        if any(not isinstance(v, str) or not v.strip() for v in (
            self.dataset_version, self.instrument_id, self.session_id, self.strategy_id,
            self.behavior_policy_version, self.account_id, self.episode_id,
        )):
            raise TrajectoryError("all episode identity fields are required")


@dataclass(frozen=True, slots=True)
class Transition:
    episode: EpisodeKey
    decision_id: str
    action_decision_ts: datetime
    first_eligible_fill_ts: datetime
    state: dict[str, Any]
    eligible_actions: tuple[str, ...]
    chosen_action: str
    propensity_status: PropensityStatus
    action_probability: float | None
    outcome_status: str
    fill: dict[str, Any] | None
    reward: float | None
    next_state: dict[str, Any] | None
    done: bool
    evidence: TransitionEvidence
    provenance_sha256: str
    source_event_sha256: str
    rejected_reason_codes: tuple[str, ...] = ()
    row_sha256: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.episode, EpisodeKey) or not self.decision_id.strip():
            raise TrajectoryError("typed episode and decision identity are required")
        decision = _aware(self.action_decision_ts, "action_decision_ts")
        fill = _aware(self.first_eligible_fill_ts, "first_eligible_fill_ts")
        if fill < decision:
            raise TrajectoryError("first eligible fill cannot precede action decision")
        if not self.eligible_actions or any(not x.strip() for x in self.eligible_actions):
            raise TrajectoryError("eligible action support set is required")
        if len(self.eligible_actions) != len(set(self.eligible_actions)):
            raise TrajectoryError("eligible actions must be unique")
        if not self.chosen_action.strip():
            raise TrajectoryError("chosen_action is required, including explicit NO_TRADE")
        if self.outcome_status in {"REJECTED", "NO_TRADE", "ABSTAINED"}:
            if self.chosen_action not in self.eligible_actions:
                raise TrajectoryError("rejected/abstained action must be present in the eligible support set")
            if not self.rejected_reason_codes and self.chosen_action != "NO_TRADE":
                raise TrajectoryError("rejected or abstained actions require reason codes")
        elif self.chosen_action not in self.eligible_actions:
            raise TrajectoryError("chosen action is outside behavior-policy support")
        if not isinstance(self.propensity_status, PropensityStatus):
            raise TrajectoryError("propensity_status must be explicit")
        if self.propensity_status is PropensityStatus.KNOWN:
            if (self.action_probability is None or not math.isfinite(self.action_probability)
                    or not 0 < self.action_probability <= 1):
                raise TrajectoryError("known action propensity must be in (0, 1]")
        elif self.action_probability is not None:
            raise TrajectoryError("unavailable propensity cannot carry a fabricated probability")
        if self.reward is not None and not math.isfinite(self.reward):
            raise TrajectoryError("reward must be finite when resolved")
        if (self.reward is None) != (self.next_state is None):
            raise TrajectoryError("unresolved reward and next state must both be absent")
        if self.done and self.reward is None:
            raise TrajectoryError("terminal transition needs a resolved reward")
        if self.fill is not None and self.outcome_status in {"NO_TRADE", "REJECTED", "ABSTAINED", "PENDING", "UNKNOWN"}:
            raise TrajectoryError("non-executed or unresolved transition cannot contain a fill")
        if self.evidence is TransitionEvidence.HISTORICAL_REPLAY and self.outcome_status == "OBSERVED_FILL":
            raise TrajectoryError("historical replay fill cannot be labeled observed")
        if self.evidence is TransitionEvidence.DETERMINISTIC_FIXTURE and self.outcome_status == "OBSERVED_FILL":
            raise TrajectoryError("fixture fill cannot be labeled observed")
        for value, name in ((self.provenance_sha256, "provenance_sha256"),
                            (self.source_event_sha256, "source_event_sha256")):
            _hash(value, name)
        if not isinstance(self.evidence, TransitionEvidence):
            raise TrajectoryError("evidence class must be explicit")
        object.__setattr__(self, "action_decision_ts", decision)
        object.__setattr__(self, "first_eligible_fill_ts", fill)
        # Freeze a canonical JSON-compatible snapshot so callers cannot mutate state after hashing.
        state = _freeze(_canonical(self.state))
        next_state = _freeze(_canonical(self.next_state)) if self.next_state is not None else None
        execution = _freeze(_canonical(self.fill)) if self.fill is not None else None
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "next_state", next_state)
        object.__setattr__(self, "fill", execution)
        digest = hashlib.sha256(json.dumps(self._payload(), sort_keys=True, separators=(",", ":"),
                                          allow_nan=False).encode()).hexdigest()
        if self.row_sha256 and _hash(self.row_sha256, "row_sha256") != digest:
            raise TrajectoryError("provided row_sha256 does not match transition content")
        object.__setattr__(self, "row_sha256", digest)

    def _payload(self) -> dict[str, Any]:
        def thaw(value: Any) -> Any:
            if isinstance(value, dict) or isinstance(value, MappingProxyType):
                return {key: thaw(item) for key, item in value.items()}
            if isinstance(value, tuple):
                return [thaw(item) for item in value]
            return value

        return {
            "episode": {name: getattr(self.episode, name) for name in self.episode.__dataclass_fields__},
            "decision_id": self.decision_id,
            "action_decision_ts": self.action_decision_ts.isoformat(),
            "first_eligible_fill_ts": self.first_eligible_fill_ts.isoformat(),
            "state": thaw(self.state),
            "eligible_actions": self.eligible_actions,
            "chosen_action": self.chosen_action,
            "propensity_status": self.propensity_status.value,
            "action_probability": self.action_probability,
            "outcome_status": self.outcome_status,
            "fill": thaw(self.fill),
            "reward": self.reward,
            "next_state": thaw(self.next_state),
            "done": self.done,
            "evidence": self.evidence.value,
            "provenance_sha256": self.provenance_sha256,
            "source_event_sha256": self.source_event_sha256,
            "rejected_reason_codes": self.rejected_reason_codes,
        }


__all__ = ["EpisodeKey", "PropensityStatus", "TrajectoryError", "Transition", "TransitionEvidence"]
