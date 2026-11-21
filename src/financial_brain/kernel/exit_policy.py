"""Versioned, explicit position exit rules for paper/research trade plans."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from .trader_signal import ExitTrigger


class ExitPolicyError(ValueError):
    """Exit policy is incomplete, contradictory, or malformed."""


class ExitRuleAction(StrEnum):
    FULL_EXIT = "FULL_EXIT"
    SCALE_OUT = "SCALE_OUT"
    MOVE_STOP_TO_BREAKEVEN = "MOVE_STOP_TO_BREAKEVEN"
    TRAIL_STOP = "TRAIL_STOP"
    TIME_EXIT = "TIME_EXIT"


@dataclass(frozen=True, slots=True)
class ExitRule:
    rule_id: str
    trigger: ExitTrigger
    action: ExitRuleAction
    quantity_fraction: Decimal | None = None
    trigger_price: Decimal | None = None
    trailing_distance: Decimal | None = None
    after_sessions: int | None = None

    def __post_init__(self) -> None:
        if not self.rule_id.strip():
            raise ExitPolicyError("rule_id is required")
        if not isinstance(self.trigger, ExitTrigger) or not isinstance(self.action, ExitRuleAction):
            raise ExitPolicyError("trigger and action must use typed enums")
        for name in ("quantity_fraction", "trigger_price", "trailing_distance"):
            raw = getattr(self, name)
            if raw is None:
                continue
            try:
                value = Decimal(raw)
            except (InvalidOperation, TypeError) as exc:
                raise ExitPolicyError(f"{name} must be decimal") from exc
            if not value.is_finite() or value <= 0:
                raise ExitPolicyError(f"{name} must be finite and positive")
            object.__setattr__(self, name, value)
        if self.action in {ExitRuleAction.FULL_EXIT, ExitRuleAction.SCALE_OUT, ExitRuleAction.TIME_EXIT}:
            if self.quantity_fraction is None or self.quantity_fraction > 1:
                raise ExitPolicyError("exit rules require a quantity fraction in (0, 1]")
        elif self.quantity_fraction is not None:
            raise ExitPolicyError("stop adjustment rules cannot carry an exit quantity")
        if self.action is ExitRuleAction.FULL_EXIT and self.quantity_fraction != 1:
            raise ExitPolicyError("FULL_EXIT must use quantity_fraction=1")
        if self.action is ExitRuleAction.SCALE_OUT and self.quantity_fraction == 1:
            raise ExitPolicyError("SCALE_OUT must leave some quantity open")
        if self.action is ExitRuleAction.TRAIL_STOP and self.trailing_distance is None:
            raise ExitPolicyError("TRAIL_STOP requires a positive trailing_distance")
        if self.action is not ExitRuleAction.TRAIL_STOP and self.trailing_distance is not None:
            raise ExitPolicyError("trailing_distance is valid only for TRAIL_STOP")
        if self.action is ExitRuleAction.TIME_EXIT:
            if self.trigger is not ExitTrigger.TIME_STOP or self.after_sessions is None or self.after_sessions < 1:
                raise ExitPolicyError("TIME_EXIT requires TIME_STOP and positive after_sessions")
        elif self.after_sessions is not None:
            raise ExitPolicyError("after_sessions is valid only for TIME_EXIT")
        if self.trigger_price is not None and self.trigger not in {ExitTrigger.HARD_STOP, ExitTrigger.TAKE_PROFIT}:
            raise ExitPolicyError("trigger_price is valid only for hard stop or take profit")


@dataclass(frozen=True, slots=True)
class ExitPolicy:
    strategy_id: str
    strategy_version: str
    policy_version: str
    rules: tuple[ExitRule, ...]

    def __post_init__(self) -> None:
        for name in ("strategy_id", "strategy_version", "policy_version"):
            if not getattr(self, name).strip():
                raise ExitPolicyError(f"{name} is required")
        if not self.rules:
            raise ExitPolicyError("exit policy requires at least one rule")
        ids = [rule.rule_id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ExitPolicyError("exit rule IDs must be unique")
        if not any(rule.trigger is ExitTrigger.HARD_STOP for rule in self.rules):
            raise ExitPolicyError("exit policy must define hard-stop protection")
        scale_fraction = sum((rule.quantity_fraction or Decimal(0) for rule in self.rules
                              if rule.action is ExitRuleAction.SCALE_OUT), Decimal(0))
        if scale_fraction > 1:
            raise ExitPolicyError("scale-out fractions cannot exceed the full position")

    @property
    def policy_hash(self) -> str:
        payload = {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "policy_version": self.policy_version,
            "rules": [{
                "rule_id": rule.rule_id, "trigger": rule.trigger.value, "action": rule.action.value,
                "quantity_fraction": str(rule.quantity_fraction) if rule.quantity_fraction is not None else None,
                "trigger_price": str(rule.trigger_price) if rule.trigger_price is not None else None,
                "trailing_distance": str(rule.trailing_distance) if rule.trailing_distance is not None else None,
                "after_sessions": rule.after_sessions,
            } for rule in self.rules],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["ExitPolicy", "ExitPolicyError", "ExitRule", "ExitRuleAction"]
