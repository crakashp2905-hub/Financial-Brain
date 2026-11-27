"""Frozen portfolio review cadence with timestamped event-exit audit decisions."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from financial_brain.kernel.position_signals import EmittedExitEvent


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CadenceError(ValueError):
    """Invalid frozen schedule, review cursor or triggered exit evidence."""


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CadenceError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _hash(value: str, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CadenceError(f"{label} must be a lowercase SHA-256")


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


class CadenceActionKind(StrEnum):
    SCHEDULED_REBALANCE = "SCHEDULED_REBALANCE"
    EVENT_RISK_EXIT = "EVENT_RISK_EXIT"


@dataclass(frozen=True, slots=True)
class FrozenCadencePolicy:
    policy_id: str
    policy_version: str
    frozen_at: datetime
    scheduled_rebalance_at: tuple[datetime, ...]
    schedule_source_sha256: str

    def __post_init__(self) -> None:
        if not self.policy_id.strip() or not self.policy_version.strip():
            raise CadenceError("policy identity and version are required")
        frozen = _utc(self.frozen_at, "policy frozen_at")
        schedule = tuple(_utc(value, "scheduled rebalance time") for value in self.scheduled_rebalance_at)
        if not schedule or schedule != tuple(sorted(set(schedule))):
            raise CadenceError("rebalance schedule must be non-empty, unique and chronological")
        if schedule[0] <= frozen:
            raise CadenceError("every scheduled rebalance must occur after the policy is frozen")
        _hash(self.schedule_source_sha256, "schedule source hash")
        object.__setattr__(self, "frozen_at", frozen)
        object.__setattr__(self, "scheduled_rebalance_at", schedule)

    def digest(self) -> str:
        return _digest({"policy_id": self.policy_id, "policy_version": self.policy_version,
                        "frozen_at": self.frozen_at.isoformat(),
                        "schedule": [value.isoformat() for value in self.scheduled_rebalance_at],
                        "schedule_source_sha256": self.schedule_source_sha256})


@dataclass(frozen=True, slots=True)
class CadenceDecision:
    kind: CadenceActionKind
    action_id: str
    occurred_at: datetime
    priority: int
    position_id: str | None
    exit_event_idempotency_key: str | None
    exit_event_sha256: str | None


@dataclass(frozen=True, slots=True)
class CadenceAudit:
    policy_sha256: str
    as_of: datetime
    decisions: tuple[CadenceDecision, ...]
    decisions_sha256: str
    promotion_allowed: bool = False


def evaluate_cadence(*, policy: FrozenCadencePolicy, as_of: datetime,
                     after: datetime | None = None,
                     emitted_risk_exits: tuple[EmittedExitEvent, ...] = ()) -> CadenceAudit:
    """Return overdue scheduled reviews and already-emitted risk exits through `as_of`.

    Risk-exit events are copied by identity into the audit output and rank before scheduled
    rebalances at the same time. The function never fabricates an exit event or submits an
    order. `after` is the durable caller cursor; replaying the same window is deterministic
    and stable action IDs let an event store enforce idempotency.
    """
    now = _utc(as_of, "as_of")
    if now < policy.frozen_at:
        raise CadenceError("evaluation cannot precede the frozen policy")
    cursor = _utc(after, "after") if after is not None else policy.frozen_at
    if cursor < policy.frozen_at or cursor > now:
        raise CadenceError("review cursor must lie between policy freeze and as-of")
    decisions: list[CadenceDecision] = []
    for scheduled_at in policy.scheduled_rebalance_at:
        if cursor < scheduled_at <= now:
            decisions.append(CadenceDecision(
                CadenceActionKind.SCHEDULED_REBALANCE,
                _digest({"policy_sha256": policy.digest(), "scheduled_at": scheduled_at.isoformat()}),
                scheduled_at, 1, None, None, None,
            ))

    seen_keys: set[str] = set()
    for event in emitted_risk_exits:
        observed = _utc(event.observed_trigger_at, "risk-exit trigger observation")
        occurred = _utc(event.exit_signal.occurred_at, "risk-exit event time")
        if event.idempotency_key in seen_keys:
            raise CadenceError("duplicate risk-exit idempotency key")
        seen_keys.add(event.idempotency_key)
        if observed > occurred:
            raise CadenceError("risk-exit event precedes or omits its trigger observation")
        if occurred > now:
            raise CadenceError("future risk-exit events cannot enter a current cadence audit")
        if occurred <= cursor:
            continue
        event_body = {
            "idempotency_key": event.idempotency_key,
            "signal_id": event.exit_signal.signal_id,
            "position_id": event.exit_signal.closes_position_id,
            "parent_signal_id": event.exit_signal.parent_signal_id,
            "strategy_id": event.exit_signal.strategy_id,
            "strategy_version": event.exit_signal.strategy_version,
            "instrument_id": event.exit_signal.instrument_id,
            "closing_action": event.exit_signal.closing_action.value,
            "source_hash": event.exit_signal.source_hash,
            "trigger": event.trigger.value,
            "observed_trigger_at": observed.isoformat(),
            "occurred_at": occurred.isoformat(),
            "quantity": str(event.quantity),
            "reason": event.exit_signal.reason,
        }
        decisions.append(CadenceDecision(CadenceActionKind.EVENT_RISK_EXIT,
                                         _digest(event_body), occurred, 0,
                                         event.exit_signal.closes_position_id,
                                         event.idempotency_key, _digest(event_body)))
    ordered = tuple(sorted(decisions, key=lambda row: (row.occurred_at, row.priority, row.action_id)))
    report = {"policy_sha256": policy.digest(), "as_of": now.isoformat(),
              "decisions": [{"kind": row.kind.value, "action_id": row.action_id,
                             "occurred_at": row.occurred_at.isoformat(), "priority": row.priority,
                             "position_id": row.position_id,
                             "exit_event_idempotency_key": row.exit_event_idempotency_key,
                             "exit_event_sha256": row.exit_event_sha256} for row in ordered],
              "promotion_allowed": False}
    return CadenceAudit(policy.digest(), now, ordered, _digest(report))


__all__ = ["CadenceActionKind", "CadenceAudit", "CadenceDecision", "CadenceError",
           "FrozenCadencePolicy", "evaluate_cadence"]
