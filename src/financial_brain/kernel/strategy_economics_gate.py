"""Versioned strategy-specific economic gates; never impose one universal ratio."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from .trade_economics import TradeEconomics


class StrategyEconomicsGateError(ValueError):
    """Invalid or unsubstantiated strategy economics threshold."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _decimal(name: str, raw: Decimal | str | int) -> Decimal:
    try:
        value = raw if isinstance(raw, Decimal) else Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise StrategyEconomicsGateError(f"{name} must be decimal") from exc
    if not value.is_finite():
        raise StrategyEconomicsGateError(f"{name} must be finite")
    return value


@dataclass(frozen=True, slots=True)
class StrategyEconomicsRule:
    strategy_id: str
    strategy_version: str
    minimum_net_reward_to_risk: Decimal
    minimum_expected_net_inr: Decimal
    calibration_start: date
    calibration_end: date
    evidence_sha256: str
    rule_version: str

    def __post_init__(self) -> None:
        for name in ("strategy_id", "strategy_version", "rule_version"):
            if not getattr(self, name).strip():
                raise StrategyEconomicsGateError(f"{name} is required")
        ratio = _decimal("minimum_net_reward_to_risk", self.minimum_net_reward_to_risk)
        expected = _decimal("minimum_expected_net_inr", self.minimum_expected_net_inr)
        if ratio < 0:
            raise StrategyEconomicsGateError("minimum_net_reward_to_risk cannot be negative")
        if self.calibration_start >= self.calibration_end:
            raise StrategyEconomicsGateError("calibration period must have positive duration")
        if not _SHA256.fullmatch(self.evidence_sha256):
            raise StrategyEconomicsGateError("evidence_sha256 must be lowercase SHA-256")
        object.__setattr__(self, "minimum_net_reward_to_risk", ratio)
        object.__setattr__(self, "minimum_expected_net_inr", expected)


@dataclass(frozen=True, slots=True)
class StrategyEconomicsDecision:
    eligible_for_research: bool
    reasons: tuple[str, ...]
    forecast_is_uncertain: bool = True


def evaluate_strategy_economics(
    rule: StrategyEconomicsRule,
    economics: TradeEconomics,
    *,
    strategy_id: str,
    strategy_version: str,
    expected_net_inr: Decimal | str | int,
) -> StrategyEconomicsDecision:
    """Check one frozen rule; a passing estimate remains a research candidate only."""
    if (strategy_id, strategy_version) != (rule.strategy_id, rule.strategy_version):
        raise StrategyEconomicsGateError("rule does not match strategy identity/version")
    expected = _decimal("expected_net_inr", expected_net_inr)
    reasons: list[str] = []
    if economics.estimated_net_reward_to_risk < rule.minimum_net_reward_to_risk:
        reasons.append("NET_REWARD_RISK_BELOW_STRATEGY_THRESHOLD")
    if expected < rule.minimum_expected_net_inr:
        reasons.append("EXPECTED_NET_BELOW_STRATEGY_THRESHOLD")
    return StrategyEconomicsDecision(not reasons, tuple(reasons))


__all__ = ["StrategyEconomicsDecision", "StrategyEconomicsGateError", "StrategyEconomicsRule",
           "evaluate_strategy_economics"]
