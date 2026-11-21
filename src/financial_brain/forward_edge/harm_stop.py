"""Predeclared stop-for-harm rules and no-early-promotion decisions (FE-0101)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone


class HarmStopError(ValueError):
    """Invalid prospective harm-stop policy or session record."""


@dataclass(frozen=True)
class HarmStopPlan:
    plan_id: str
    frozen_at: datetime
    first_eligible_session: date
    max_daily_loss_fraction: float
    max_drawdown_fraction: float
    minimum_sessions_before_review: int = 60
    stop_on_ledger_mismatch: bool = True
    stop_on_critical_incident: bool = True

    def __post_init__(self) -> None:
        if not self.plan_id.strip():
            raise HarmStopError("plan_id is required")
        if self.frozen_at.tzinfo is None or self.frozen_at.utcoffset() is None:
            raise HarmStopError("frozen_at must be timezone aware")
        if self.frozen_at.date() >= self.first_eligible_session:
            raise HarmStopError("harm thresholds must be frozen before the first eligible session")
        for name, value in (("max_daily_loss_fraction", self.max_daily_loss_fraction),
                            ("max_drawdown_fraction", self.max_drawdown_fraction)):
            if not math.isfinite(value) or not 0 < value < 1:
                raise HarmStopError(f"{name} must be in (0, 1)")
        if type(self.minimum_sessions_before_review) is not int or self.minimum_sessions_before_review < 1:
            raise HarmStopError("minimum_sessions_before_review must be positive")

    def digest(self) -> str:
        body = {"plan_id": self.plan_id,
                "frozen_at": self.frozen_at.astimezone(timezone.utc).isoformat(),
                "first_eligible_session": self.first_eligible_session.isoformat(),
                "max_daily_loss_fraction": self.max_daily_loss_fraction,
                "max_drawdown_fraction": self.max_drawdown_fraction,
                "minimum_sessions_before_review": self.minimum_sessions_before_review,
                "stop_on_ledger_mismatch": self.stop_on_ledger_mismatch,
                "stop_on_critical_incident": self.stop_on_critical_incident}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class HarmSession:
    session: date
    net_return: float
    ledger_reconciled: bool
    critical_incident_id: str | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.net_return) or self.net_return <= -1:
            raise HarmStopError("session return must be finite and cannot exhaust equity")
        if self.critical_incident_id is not None and not self.critical_incident_id.strip():
            raise HarmStopError("critical incident ID cannot be blank")


@dataclass(frozen=True)
class HarmStopDecision:
    plan_hash: str
    evaluated_sessions: int
    peak_relative_equity: float
    ending_relative_equity: float
    max_drawdown_observed: float
    status: str
    trigger_session: date | None
    trigger_reason: str | None
    promotion_allowed: bool


def evaluate_harm_stop(plan: HarmStopPlan, sessions: tuple[HarmSession, ...]) -> HarmStopDecision:
    """Stop at the first harm trigger; otherwise continue/review without promotion."""
    dates = tuple(row.session for row in sessions)
    if tuple(sorted(dates)) != dates or len(set(dates)) != len(dates):
        raise HarmStopError("session records must be unique and chronological")
    if any(day < plan.first_eligible_session for day in dates):
        raise HarmStopError("session predates harm-stop cohort")
    equity = peak = 1.0
    max_drawdown = 0.0
    for index, row in enumerate(sessions):
        equity *= 1.0 + row.net_return
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak
        max_drawdown = max(max_drawdown, drawdown)
        reason = None
        if row.net_return <= -plan.max_daily_loss_fraction:
            reason = "MAX_DAILY_LOSS_BREACHED"
        elif drawdown >= plan.max_drawdown_fraction:
            reason = "MAX_DRAWDOWN_BREACHED"
        elif plan.stop_on_ledger_mismatch and not row.ledger_reconciled:
            reason = "LEDGER_RECONCILIATION_MISMATCH"
        elif plan.stop_on_critical_incident and row.critical_incident_id:
            reason = f"CRITICAL_INCIDENT:{row.critical_incident_id}"
        if reason:
            return HarmStopDecision(plan.digest(), index + 1, peak, equity, max_drawdown,
                                    "STOP_FOR_HARM", row.session, reason, False)
    status = "REVIEW_ONLY" if len(sessions) >= plan.minimum_sessions_before_review else "CONTINUE"
    return HarmStopDecision(plan.digest(), len(sessions), peak, equity, max_drawdown,
                            status, None, None, False)
