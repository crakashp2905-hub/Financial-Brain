"""Execution-policy reward from parent-order fills and an explicit non-fill cost.

This scores implementation only. It does not approve a parent, generate alpha,
simulate queue position, or imply that a simulated fill was broker executed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal


class ExecutionRewardError(ValueError):
    """Invalid parent order or fill evidence for execution scoring."""


EvidenceMode = Literal["BROKER_EXECUTED", "REPLAYED_OBSERVED", "SIMULATED"]


@dataclass(frozen=True, slots=True)
class ParentOrder:
    order_id: str
    side: Literal["BUY", "SELL"]
    approved_quantity: int
    arrival_price: float
    approved_at: datetime
    deadline: datetime
    alpha_decay_cost_per_share: float


@dataclass(frozen=True, slots=True)
class ExecutionFill:
    fill_id: str
    quantity: int
    price: float
    fees_inr: float
    filled_at: datetime
    evidence_mode: EvidenceMode


def score_execution(parent: ParentOrder, fills: tuple[ExecutionFill, ...]) -> dict[str, object]:
    """Return a lower-is-better INR cost and its negative reward.

    Shortfall is signed against the immutable approved parent arrival price.
    Unfilled quantity is charged the caller-supplied alpha-decay cost per share,
    so a policy cannot improve its score simply by refusing to execute. Fill
    evidence is labeled and never upgraded by this function.
    """
    if not parent.order_id.strip() or parent.side not in {"BUY", "SELL"}:
        raise ExecutionRewardError("approved parent identity and side are required")
    if type(parent.approved_quantity) is not int or parent.approved_quantity <= 0:
        raise ExecutionRewardError("approved parent quantity must be a positive integer")
    if not math.isfinite(parent.arrival_price) or parent.arrival_price <= 0:
        raise ExecutionRewardError("arrival price must be finite and positive")
    if not math.isfinite(parent.alpha_decay_cost_per_share) or parent.alpha_decay_cost_per_share <= 0:
        raise ExecutionRewardError("positive explicit alpha-decay cost per share is required")
    approved_at, deadline = _utc(parent.approved_at), _utc(parent.deadline)
    if deadline <= approved_at:
        raise ExecutionRewardError("execution deadline must follow parent approval")
    if len({fill.fill_id for fill in fills}) != len(fills):
        raise ExecutionRewardError("fill IDs must be unique")

    filled_quantity = 0
    shortfall = 0.0
    fees = 0.0
    modes: set[str] = set()
    for fill in fills:
        if not fill.fill_id.strip() or type(fill.quantity) is not int or fill.quantity <= 0:
            raise ExecutionRewardError("each fill needs an ID and positive integer quantity")
        if not math.isfinite(fill.price) or fill.price <= 0 or not math.isfinite(fill.fees_inr) or fill.fees_inr < 0:
            raise ExecutionRewardError("fill price and fees must be finite and valid")
        if fill.evidence_mode not in {"BROKER_EXECUTED", "REPLAYED_OBSERVED", "SIMULATED"}:
            raise ExecutionRewardError("fill evidence mode is unsupported")
        filled_at = _utc(fill.filled_at)
        if not approved_at <= filled_at <= deadline:
            raise ExecutionRewardError("fill lies outside the approved parent execution window")
        filled_quantity += fill.quantity
        if filled_quantity > parent.approved_quantity:
            raise ExecutionRewardError("fills exceed immutable approved parent quantity")
        sign = 1.0 if parent.side == "BUY" else -1.0
        shortfall += sign * (fill.price - parent.arrival_price) * fill.quantity
        fees += fill.fees_inr
        modes.add(fill.evidence_mode)

    residual = parent.approved_quantity - filled_quantity
    non_fill_penalty = residual * parent.alpha_decay_cost_per_share
    total_cost = shortfall + fees + non_fill_penalty
    return {
        "parent_order_id": parent.order_id,
        "approved_quantity": parent.approved_quantity,
        "filled_quantity": filled_quantity,
        "residual_quantity": residual,
        "implementation_shortfall_inr": shortfall,
        "fees_inr": fees,
        "alpha_decay_non_fill_penalty_inr": non_fill_penalty,
        "total_execution_cost_inr": total_cost,
        "reward_inr": -total_cost,
        "evidence_modes": sorted(modes),
        "evidence_label": (next(iter(modes)) if len(modes) == 1 else "MIXED" if modes else "NO_FILL"),
        "parent_approval_immutable": True,
    }


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionRewardError("execution timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


__all__ = ["ExecutionFill", "ExecutionRewardError", "ParentOrder", "score_execution"]
