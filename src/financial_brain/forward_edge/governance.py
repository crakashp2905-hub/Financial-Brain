"""Model learning and champion/challenger governance (FE-0124 .. FE-0131). Offline; no model is trained here."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from financial_brain.forward_edge.approvals import Approval, ApprovalError, validate_approval


class GovernanceError(ValueError):
    pass


# ---- FE-0124 outcome maturation ----

class MaturationQueue:
    """A label is released only after the full exit plus an embargo; unfinished outcomes never reach training."""

    def __init__(self, embargo: timedelta) -> None:
        self.embargo = embargo
        self._items: dict[str, datetime] = {}

    def add(self, example_id: str, exit_at: datetime) -> None:
        if exit_at.tzinfo is None:
            raise GovernanceError("exit time must be timezone-aware")
        if example_id in self._items:
            raise GovernanceError("example already queued")
        self._items[example_id] = exit_at.astimezone(timezone.utc)

    def mature(self, now: datetime) -> list[str]:
        return sorted(k for k, t in self._items.items() if t + self.embargo <= now.astimezone(timezone.utc))


# ---- FE-0125 reproducible training lineage ----

def model_id(*, data_sha256: str, feature_schema_sha256: str, params: dict[str, str], seed: int, code_sha256: str) -> str:
    body = json.dumps({"data": data_sha256, "schema": feature_schema_sha256, "params": params, "seed": seed,
                       "code": code_sha256}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ---- FE-0126 contamination guard ----

def assert_search_data_clear(search_dates: set[date], locked_cohort_dates: set[date]) -> None:
    overlap = sorted(search_dates & locked_cohort_dates)
    if overlap:
        raise GovernanceError(f"hyperparameter search touches a locked cohort: {overlap[0]}")


# ---- FE-0127 challenger vs champion ----

def challenger_beats_champion(*, mean_diff_net: float, se_diff: float, candidates_tested: int, z_single: float = 1.96) -> bool:
    """Net, aligned-capital difference must clear a Bonferroni-widened bound for the number of candidates tested."""
    if candidates_tested < 1 or se_diff <= 0:
        raise GovernanceError("need a positive standard error and at least one candidate")
    from statistics import NormalDist
    alpha = 0.05 / candidates_tested
    z = NormalDist().inv_cdf(1 - alpha / 2)
    return mean_diff_net - z * se_diff > 0


# ---- FE-0128 promotion restriction ----

def promotion_allowed(*, status: str, preauthorized: bool, verified_sessions: int, min_sessions: int = 60) -> tuple[bool, str]:
    if not preauthorized:
        return False, "NOT_PREAUTHORIZED"
    if status not in ("FROZEN_CHALLENGER", "VERIFIED"):
        return False, "STATUS_NOT_PROMOTABLE"
    if verified_sessions < min_sessions:
        return False, "NO_TRUSTED_RECEIPT_COVERAGE"
    return True, "OK"


# ---- FE-0129 human approval for economic risk ----

ECONOMIC_KINDS = ("CAPITAL", "LEVERAGE", "MICRO_RISK_BUDGET", "LIVE_CASH_ENABLE")


def authorize_economic_change(kind: str, subject: str, scope_sha256: str, approval: Approval | None, now: datetime) -> None:
    if kind not in ECONOMIC_KINDS:
        raise GovernanceError("not an economic-risk change kind")
    if approval is None:
        raise GovernanceError("a human approval is required")
    try:
        validate_approval(approval, kind=kind, subject=subject, scope_sha256=scope_sha256, now=now)
    except ApprovalError as exc:
        raise GovernanceError(f"change refused: {exc}") from exc


# ---- FE-0130 transition log ----

class TransitionLog:
    def __init__(self) -> None:
        self._rows: list[dict] = []

    def record(self, kind: str, subject: str, detail: str, at: datetime) -> int:
        if kind not in ("DOWNGRADE", "DRIFT", "ROLLBACK", "PROMOTION", "HUMAN_EXCEPTION"):
            raise GovernanceError("unknown transition kind")
        self._rows.append({"kind": kind, "subject": subject, "detail": detail, "at": at.isoformat()})
        return len(self._rows)

    def rows(self) -> list[dict]:
        return [dict(r) for r in self._rows]

    def delete(self, index: int) -> None:
        raise GovernanceError("transitions are never deleted")


# ---- FE-0131 safe-policy reversion ----

@dataclass
class SafetyGovernor:
    active_policy: str = "CANDIDATE"
    new_orders_allowed: bool = True
    cancelled_open_orders: int = 0

    def observe(self, event: str, open_orders: int = 0) -> None:
        if event in ("DRIFT", "OUTAGE", "ACCOUNTING_MISMATCH"):
            self.active_policy = "FROZEN_SAFE"
            self.new_orders_allowed = False
            self.cancelled_open_orders += open_orders
