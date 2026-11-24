"""Typed contracts for candidate-independent hierarchical paper-policy decisions."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Sequence

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
LEVELS = ("GLOBAL", "DESK", "FAMILY", "INSTRUMENT", "EXECUTION")


class HierarchyError(ValueError):
    """Invalid hierarchical decision evidence."""


class ActionKind(StrEnum):
    CASH = "CASH"
    WAIT = "WAIT"
    CLOSE = "CLOSE"
    OPEN = "OPEN"


@dataclass(frozen=True, slots=True)
class PolicyAction:
    kind: ActionKind
    target_id: str | None = None
    candidate_independent: bool = False

    def __post_init__(self) -> None:
        if self.kind in {ActionKind.OPEN, ActionKind.CLOSE} and not (self.target_id or "").strip():
            raise HierarchyError("OPEN and CLOSE actions require a target identity")
        if self.kind in {ActionKind.CASH, ActionKind.WAIT} and self.target_id is not None:
            raise HierarchyError("CASH and WAIT cannot target an instrument")
        if self.candidate_independent != (self.kind in {ActionKind.CASH, ActionKind.WAIT, ActionKind.CLOSE}):
            raise HierarchyError("only CASH, WAIT and CLOSE are candidate-independent safe actions")


def available_global_actions(*, alpha_candidate_ids: Sequence[str], open_position_ids: Sequence[str]) -> tuple[PolicyAction, ...]:
    """Return safe actions even when the alpha candidate list is empty."""
    if len(set(alpha_candidate_ids)) != len(alpha_candidate_ids):
        raise HierarchyError("alpha candidate identities must be unique")
    if len(set(open_position_ids)) != len(open_position_ids):
        raise HierarchyError("open position identities must be unique")
    if any(not value.strip() for value in (*alpha_candidate_ids, *open_position_ids)):
        raise HierarchyError("action identities cannot be blank")
    actions = [PolicyAction(ActionKind.CASH, candidate_independent=True),
               PolicyAction(ActionKind.WAIT, candidate_independent=True)]
    actions.extend(PolicyAction(ActionKind.CLOSE, position_id, candidate_independent=True)
                   for position_id in sorted(open_position_ids))
    actions.extend(PolicyAction(ActionKind.OPEN, candidate_id)
                   for candidate_id in sorted(alpha_candidate_ids))
    return tuple(actions)


@dataclass(frozen=True, slots=True)
class HierarchyDecision:
    decision_id: str
    session_id: str
    level: str
    parent_decision_id: str | None
    proposed_action: PolicyAction
    occurred_at_utc: datetime

    def __post_init__(self) -> None:
        if not self.decision_id.strip() or not self.session_id.strip() or self.level not in LEVELS:
            raise HierarchyError("decision, session and valid hierarchy level are required")
        if self.occurred_at_utc.tzinfo is None or self.occurred_at_utc.utcoffset() is None:
            raise HierarchyError("hierarchy decision time must be timezone-aware")
        object.__setattr__(self, "occurred_at_utc", self.occurred_at_utc.astimezone(timezone.utc))


@dataclass(frozen=True, slots=True)
class HierarchyDecisionChain:
    decisions: tuple[HierarchyDecision, ...]
    strategy_router_sha256: str
    regime_router_sha256: str
    risk_projected_action: PolicyAction
    risk_projection_allowed: bool
    chain_sha256: str = ""

    def __post_init__(self) -> None:
        if len(self.decisions) != len(LEVELS):
            raise HierarchyError("decision chain must contain all five hierarchy levels")
        for label, digest in (("strategy router", self.strategy_router_sha256),
                              ("regime router", self.regime_router_sha256)):
            if not _SHA256.fullmatch(digest):
                raise HierarchyError(f"{label} version must be pinned by SHA-256")
        if tuple(row.level for row in self.decisions) != LEVELS:
            raise HierarchyError("hierarchy decisions must follow global-to-execution order")
        if self.decisions[0].parent_decision_id is not None:
            raise HierarchyError("global decision cannot have a parent")
        if len({row.decision_id for row in self.decisions}) != len(LEVELS):
            raise HierarchyError("hierarchy decision IDs must be unique")
        if len({row.session_id for row in self.decisions}) != 1:
            raise HierarchyError("all hierarchy rows must belong to one market session")
        for prior, child in zip(self.decisions, self.decisions[1:]):
            if child.parent_decision_id != prior.decision_id:
                raise HierarchyError("every child decision must link to its immediate parent")
            if child.occurred_at_utc < prior.occurred_at_utc:
                raise HierarchyError("child decision cannot precede its parent")
        if not self.risk_projection_allowed and self.risk_projected_action.kind not in {
            ActionKind.CASH, ActionKind.WAIT, ActionKind.CLOSE,
        }:
            raise HierarchyError("risk rejection must project to a safe candidate-independent action")
        payload = {
            "decisions": [
                {"decision_id": row.decision_id, "session_id": row.session_id, "level": row.level,
                 "parent_decision_id": row.parent_decision_id,
                 "proposed_action": {"kind": row.proposed_action.kind.value,
                                     "target_id": row.proposed_action.target_id},
                 "occurred_at_utc": row.occurred_at_utc.isoformat()}
                for row in self.decisions
            ],
            "strategy_router_sha256": self.strategy_router_sha256,
            "regime_router_sha256": self.regime_router_sha256,
            "risk_projected_action": {"kind": self.risk_projected_action.kind.value,
                                      "target_id": self.risk_projected_action.target_id},
            "risk_projection_allowed": self.risk_projection_allowed,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                           allow_nan=False).encode()).hexdigest()
        if self.chain_sha256 and self.chain_sha256 != digest:
            raise HierarchyError("hierarchy chain hash does not match decision evidence")
        object.__setattr__(self, "chain_sha256", digest)


__all__ = ["ActionKind", "HierarchyDecision", "HierarchyDecisionChain", "HierarchyError",
           "PolicyAction", "available_global_actions"]
