"""Frozen-threshold strategy degradation monitoring for forward paper cohorts.

This module evaluates supplied, evidence-linked rolling metrics. It does not
derive market performance, tune thresholds, or change strategy parameters.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import math


class DegradationError(ValueError):
    """Invalid policy, observation, or journal transition."""


class Action(StrEnum):
    CONTINUE = "CONTINUE"
    PAUSE = "PAUSE"
    RETIRE = "RETIRE"


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DegradationError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class FrozenThresholds:
    """Thresholds fixed before the monitored paper cohort starts."""

    strategy_id: str
    strategy_version: str
    frozen_at: datetime
    cohort_starts_at: datetime
    pause_net_alpha_bps: float
    retire_net_alpha_bps: float
    max_slippage_bps: float
    min_fill_coverage: float
    max_drift_score: float
    min_observations: int = 20
    pause_after_breaches: int = 1
    retire_after_severe_breaches: int = 3

    def __post_init__(self) -> None:
        frozen = _utc(self.frozen_at, "frozen_at")
        starts = _utc(self.cohort_starts_at, "cohort_starts_at")
        if not self.strategy_id.strip() or not self.strategy_version.strip():
            raise DegradationError("strategy identity and version are required")
        if frozen >= starts:
            raise DegradationError("thresholds must be frozen before the cohort starts")
        numeric = (self.pause_net_alpha_bps, self.retire_net_alpha_bps,
                   self.max_slippage_bps, self.min_fill_coverage, self.max_drift_score)
        if not all(math.isfinite(x) for x in numeric):
            raise DegradationError("thresholds must be finite")
        if self.retire_net_alpha_bps > self.pause_net_alpha_bps:
            raise DegradationError("retire alpha threshold must be no higher than pause threshold")
        if self.max_slippage_bps < 0 or not 0 <= self.min_fill_coverage <= 1 or self.max_drift_score < 0:
            raise DegradationError("invalid slippage, fill-coverage, or drift threshold")
        if self.min_observations < 1 or self.pause_after_breaches < 1:
            raise DegradationError("observation and pause counts must be positive")
        if self.retire_after_severe_breaches < self.pause_after_breaches:
            raise DegradationError("retirement persistence cannot be shorter than pause persistence")

    @property
    def policy_sha256(self) -> str:
        payload = asdict(self)
        payload["frozen_at"] = _utc(self.frozen_at, "frozen_at").isoformat()
        payload["cohort_starts_at"] = _utc(self.cohort_starts_at, "cohort_starts_at").isoformat()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class RollingObservation:
    observation_id: str
    policy_sha256: str
    window_start: datetime
    window_end: datetime
    observed_at: datetime
    net_alpha_bps: float | None
    slippage_bps: float | None
    fill_coverage: float | None
    drift_score: float | None
    sample_count: int
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class AlarmEvent:
    observation_id: str
    strategy_id: str
    strategy_version: str
    policy_sha256: str
    action: Action
    reasons: tuple[str, ...]
    consecutive_breaches: int
    consecutive_severe_breaches: int
    observation_sha256: str


def _metric(value: float | None, name: str) -> float:
    if value is None or not math.isfinite(value):
        raise DegradationError(f"{name} is missing or non-finite")
    return value


class DegradationMonitor:
    """Append-only in-memory monitor; persist returned events in the caller's durable journal."""

    def __init__(self, policy: FrozenThresholds, prior_events: tuple[AlarmEvent, ...] = ()) -> None:
        self.policy = policy
        self._events = list(prior_events)
        self._ids: set[str] = set()
        self._breaches = 0
        self._severe = 0
        self._latched = Action.CONTINUE
        for event in self._events:
            if event.policy_sha256 != policy.policy_sha256:
                raise DegradationError("journal event belongs to a different frozen policy")
            if event.observation_id in self._ids:
                raise DegradationError("duplicate observation in journal")
            self._ids.add(event.observation_id)
            self._breaches = event.consecutive_breaches
            self._severe = event.consecutive_severe_breaches
            if event.action == Action.RETIRE:
                self._latched = Action.RETIRE
            elif event.action == Action.PAUSE and self._latched != Action.RETIRE:
                self._latched = Action.PAUSE

    @property
    def events(self) -> tuple[AlarmEvent, ...]:
        return tuple(self._events)

    def observe(self, observation: RollingObservation) -> AlarmEvent:
        policy = self.policy
        if observation.observation_id in self._ids:
            raise DegradationError("observation already recorded")
        start, end, seen = (_utc(observation.window_start, "window_start"),
                            _utc(observation.window_end, "window_end"),
                            _utc(observation.observed_at, "observed_at"))
        if observation.policy_sha256 != policy.policy_sha256:
            raise DegradationError("observation does not attest the frozen policy")
        if start < _utc(policy.cohort_starts_at, "cohort_starts_at") or end < start or seen < end:
            raise DegradationError("observation window is outside the cohort or not yet complete")
        evidence = tuple(dict.fromkeys(x.strip() for x in observation.evidence_ids if x.strip()))
        reasons: list[str] = []
        missing = observation.sample_count < policy.min_observations or not evidence
        values: dict[str, float] = {}
        try:
            values = {
                "net_alpha_bps": _metric(observation.net_alpha_bps, "net_alpha_bps"),
                "slippage_bps": _metric(observation.slippage_bps, "slippage_bps"),
                "fill_coverage": _metric(observation.fill_coverage, "fill_coverage"),
                "drift_score": _metric(observation.drift_score, "drift_score"),
            }
        except DegradationError as exc:
            missing = True
            reasons.append(str(exc))
        if observation.sample_count < policy.min_observations:
            reasons.append("minimum observation count not met")
        if not evidence:
            reasons.append("evidence IDs are missing")

        breached = False
        severe = False
        if not missing:
            if values["net_alpha_bps"] <= policy.pause_net_alpha_bps:
                breached = True
                reasons.append("rolling net alpha crossed frozen pause threshold")
            if values["slippage_bps"] > policy.max_slippage_bps:
                breached = True
                reasons.append("slippage crossed frozen maximum")
            if values["fill_coverage"] < policy.min_fill_coverage:
                breached = True
                reasons.append("fill coverage fell below frozen minimum")
            if values["drift_score"] > policy.max_drift_score:
                breached = True
                reasons.append("drift crossed frozen maximum")
            severe = values["net_alpha_bps"] <= policy.retire_net_alpha_bps
            if severe:
                reasons.append("rolling net alpha crossed frozen retirement threshold")

        self._breaches = self._breaches + 1 if breached else 0
        self._severe = self._severe + 1 if severe else 0
        action = self._latched
        if self._severe >= policy.retire_after_severe_breaches:
            action = Action.RETIRE
        elif missing or self._breaches >= policy.pause_after_breaches:
            if action != Action.RETIRE:
                action = Action.PAUSE
        elif action == Action.CONTINUE:
            action = Action.CONTINUE
        # Pause/retire are latched. Recovery requires a separately reviewed policy/cohort.
        if self._latched == Action.PAUSE and action == Action.CONTINUE:
            action = Action.PAUSE
        self._latched = action
        def digest_metric(value: float | None) -> float | str | None:
            if value is None:
                return None
            return value if math.isfinite(value) else repr(value)

        canonical = {
            "observation_id": observation.observation_id,
            "policy_sha256": observation.policy_sha256,
            "window_start": start.isoformat(), "window_end": end.isoformat(),
            "observed_at": seen.isoformat(), "net_alpha_bps": digest_metric(observation.net_alpha_bps),
            "slippage_bps": digest_metric(observation.slippage_bps),
            "fill_coverage": digest_metric(observation.fill_coverage),
            "drift_score": digest_metric(observation.drift_score), "sample_count": observation.sample_count,
            "evidence_ids": evidence,
        }
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"),
                                           allow_nan=False).encode()).hexdigest()
        event = AlarmEvent(observation.observation_id, policy.strategy_id, policy.strategy_version,
                           policy.policy_sha256, action, tuple(reasons), self._breaches, self._severe, digest)
        self._events.append(event)
        self._ids.add(observation.observation_id)
        return event
