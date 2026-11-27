"""Monotonic drawdown-based risk caps for paper portfolio allocation.

The policy is a hard post-allocator cap: it can reduce or halt a proposed risk fraction,
never increase it. The measured worst drawdown remains binding for the supplied equity
window even if equity later recovers; resuming requires an external, separately governed
window/reset decision.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass


class DrawdownCapError(ValueError):
    """Invalid equity path or drawdown risk-cap policy."""


@dataclass(frozen=True, slots=True)
class DrawdownDeleveragingPolicy:
    reduce_at: float
    halt_at: float
    maximum_risk_fraction: float = 1.0

    def __post_init__(self) -> None:
        values = (self.reduce_at, self.halt_at, self.maximum_risk_fraction)
        if any(not math.isfinite(value) for value in values):
            raise DrawdownCapError("drawdown policy values must be finite")
        if not 0 <= self.reduce_at < self.halt_at < 1:
            raise DrawdownCapError("require 0 <= reduce_at < halt_at < 1")
        if not 0 < self.maximum_risk_fraction <= 1:
            raise DrawdownCapError("maximum risk fraction must be in (0, 1]")

    def digest(self) -> str:
        body = {"reduce_at": self.reduce_at, "halt_at": self.halt_at,
                "maximum_risk_fraction": self.maximum_risk_fraction}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class DrawdownRiskDecision:
    action: str
    peak_equity: float
    current_equity: float
    maximum_observed_drawdown: float
    proposed_risk_fraction: float
    allowed_risk_fraction: float
    exposure_scale: float
    policy_sha256: str
    report_sha256: str


def cap_risk_for_drawdown(*, equity_path: tuple[float, ...], proposed_risk_fraction: float,
                          policy: DrawdownDeleveragingPolicy) -> DrawdownRiskDecision:
    """Enforce a maximum risk fraction from observed peak-to-trough equity drawdown.

    Between ``reduce_at`` and ``halt_at`` the maximum allowed exposure declines linearly;
    at/above ``halt_at`` new risk is zero. This rule applies after any allocator, so an
    optimizer cannot override the cap by increasing its proposed risk after losses.
    """
    if len(equity_path) < 2:
        raise DrawdownCapError("at least two equity observations are required")
    if any(not math.isfinite(value) or value <= 0 for value in equity_path):
        raise DrawdownCapError("equity observations must be finite and positive")
    if not math.isfinite(proposed_risk_fraction) or not 0 <= proposed_risk_fraction <= 1:
        raise DrawdownCapError("proposed risk fraction must be finite and in [0, 1]")

    peak = equity_path[0]
    worst_drawdown = 0.0
    for equity in equity_path:
        peak = max(peak, equity)
        if peak > 0:
            worst_drawdown = max(worst_drawdown, (peak - equity) / peak)
    historical_peak = max(equity_path)
    current_equity = equity_path[-1]
    if worst_drawdown >= policy.halt_at:
        action, scale = "HALT", 0.0
    elif worst_drawdown >= policy.reduce_at:
        action = "REDUCE"
        scale = (policy.halt_at - worst_drawdown) / (policy.halt_at - policy.reduce_at)
    else:
        action, scale = "NORMAL", 1.0
    cap = policy.maximum_risk_fraction * scale
    allowed = min(proposed_risk_fraction, cap)
    body = {"policy_sha256": policy.digest(), "action": action,
            "peak_equity": historical_peak, "current_equity": current_equity,
            "maximum_observed_drawdown": worst_drawdown,
            "proposed_risk_fraction": proposed_risk_fraction,
            "allowed_risk_fraction": allowed, "exposure_scale": scale,
            "equity_path": list(equity_path)}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return DrawdownRiskDecision(action, historical_peak, current_equity, worst_drawdown,
                                proposed_risk_fraction, allowed, scale, policy.digest(), digest)


__all__ = ["DrawdownCapError", "DrawdownDeleveragingPolicy", "DrawdownRiskDecision",
           "cap_risk_for_drawdown"]
