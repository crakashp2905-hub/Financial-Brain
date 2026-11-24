"""Versioned as-of volatility and regime risk throttle (FE-0110)."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Mapping


class RiskThrottleError(ValueError):
    """Invalid throttle plan, future regime input or conflicting policy version."""


@dataclass(frozen=True)
class RiskThrottlePlan:
    plan_id: str
    frozen_at: datetime
    first_eligible_session: date
    target_volatility: float
    regime_multipliers: Mapping[str, float]
    unknown_regime_multiplier: float = 0.0

    def __post_init__(self) -> None:
        if not self.plan_id.strip() or not self.regime_multipliers:
            raise RiskThrottleError("plan ID and frozen regime thresholds are required")
        if self.frozen_at.tzinfo is None or self.frozen_at.utcoffset() is None:
            raise RiskThrottleError("frozen_at must be timezone aware")
        if self.frozen_at.date() >= self.first_eligible_session:
            raise RiskThrottleError("throttle thresholds must predate first eligible session")
        if not math.isfinite(self.target_volatility) or self.target_volatility <= 0:
            raise RiskThrottleError("target volatility must be finite and positive")
        values = [*self.regime_multipliers.values(), self.unknown_regime_multiplier]
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
            raise RiskThrottleError("risk multipliers must be in [0, 1]")
        if any(not regime.strip() for regime in self.regime_multipliers):
            raise RiskThrottleError("regime labels cannot be blank")

    def body(self) -> dict[str, object]:
        return {"plan_id": self.plan_id, "frozen_at": self.frozen_at.astimezone(timezone.utc).isoformat(),
                "first_eligible_session": self.first_eligible_session.isoformat(),
                "target_volatility": self.target_volatility,
                "regime_multipliers": dict(sorted(self.regime_multipliers.items())),
                "unknown_regime_multiplier": self.unknown_regime_multiplier}

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.body(), sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class ThrottleDecision:
    plan_hash: str
    session: date
    regime: str
    realized_volatility: float
    regime_multiplier: float
    volatility_multiplier: float
    final_multiplier: float
    reason: str


def calculate_risk_throttle(plan: RiskThrottlePlan, *, session: date, regime: str,
                            realized_volatility: float, regime_observed_at: datetime,
                            decision_at: datetime, regime_source_hash: str) -> ThrottleDecision:
    """Apply only a frozen regime table and volatility target to data available by decision time."""
    for stamp in (regime_observed_at, decision_at):
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise RiskThrottleError("regime and decision clocks must be timezone aware")
    if regime_observed_at > decision_at:
        raise RiskThrottleError("future regime observation is not allowed")
    if session < plan.first_eligible_session or decision_at.date() < plan.first_eligible_session:
        raise RiskThrottleError("session predates throttle plan eligibility")
    if not math.isfinite(realized_volatility) or realized_volatility < 0:
        raise RiskThrottleError("realized volatility must be finite and non-negative")
    if not regime_source_hash.strip():
        raise RiskThrottleError("regime source hash is required")
    known_regime = regime in plan.regime_multipliers
    regime_multiplier = plan.regime_multipliers.get(regime, plan.unknown_regime_multiplier)
    volatility_multiplier = (1.0 if realized_volatility == 0 else
                             min(1.0, plan.target_volatility / realized_volatility))
    final = regime_multiplier * volatility_multiplier
    if not known_regime or final == 0:
        reason = "UNKNOWN_REGIME_NO_RISK" if not known_regime else "REGIME_DISABLED"
    elif volatility_multiplier < 1:
        reason = "VOLATILITY_TARGET_REDUCTION"
    else:
        reason = "FROZEN_THRESHOLDS_UNCHANGED"
    return ThrottleDecision(plan.digest(), session, regime, realized_volatility,
                            regime_multiplier, volatility_multiplier, final, reason)


class RiskThrottleRegistry:
    """Immutable SQLite registry for risk thresholds used by forward sessions."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("CREATE TABLE IF NOT EXISTS throttle_plans (plan_id TEXT PRIMARY KEY, digest TEXT NOT NULL, plan_json TEXT NOT NULL)")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS throttle_plans_no_update BEFORE UPDATE ON throttle_plans BEGIN SELECT RAISE(ABORT,'throttle plans are immutable'); END")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS throttle_plans_no_delete BEFORE DELETE ON throttle_plans BEGIN SELECT RAISE(ABORT,'throttle plans are immutable'); END")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def freeze(self, plan: RiskThrottlePlan) -> bool:
        encoded = json.dumps(plan.body(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        prior = self._con.execute("SELECT digest,plan_json FROM throttle_plans WHERE plan_id=?", [plan.plan_id]).fetchone()
        if prior:
            if prior != (plan.digest(), encoded):
                raise RiskThrottleError("risk thresholds are immutable under this plan ID")
            return False
        self._con.execute("INSERT INTO throttle_plans VALUES (?,?,?)", [plan.plan_id, plan.digest(), encoded])
        self._con.commit()
        return True
