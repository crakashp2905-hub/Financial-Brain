"""Reproducible open-interest and expiry-based futures roll planning."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Sequence


class RollScheduleError(ValueError):
    """Invalid roll schedule or unproven open-interest observations."""


@dataclass(frozen=True)
class OISnapshot:
    session_date: date
    front_contract_id: str
    next_contract_id: str
    front_open_interest: int
    next_open_interest: int
    front_expiry: date
    next_expiry: date
    source_id: str
    source_sha256: str


@dataclass(frozen=True)
class RollDecision:
    session_date: date
    decision: str
    reason: str
    front_contract_id: str
    next_contract_id: str
    open_interest_source_sha256: str


@dataclass(frozen=True)
class RollSchedule:
    decisions: tuple[RollDecision, ...]
    policy_sha256: str
    evidence_sha256: str


def build_roll_schedule(
    snapshots: Sequence[OISnapshot], *,
    confirmation_sessions: int = 2,
    force_calendar_days_before_expiry: int = 1,
) -> RollSchedule:
    """Plan a roll on confirmed next-month OI leadership or before expiry.

    This proposes a paper roll only. It requires same-session sourced OI for the active
    front/next pair, uses a fixed confirmation rule, and always forces a decision by the
    configured calendar-day expiry buffer. Contract terms and roll prices remain explicit
    inputs to ``FuturesPaperAccount.roll``.
    """
    if type(confirmation_sessions) is not int or confirmation_sessions < 1:
        raise RollScheduleError("confirmation_sessions must be a positive integer")
    if type(force_calendar_days_before_expiry) is not int or force_calendar_days_before_expiry < 0:
        raise RollScheduleError("expiry buffer must be a non-negative integer")
    rows = tuple(snapshots)
    if not rows:
        raise RollScheduleError("at least one sourced OI snapshot is required")
    dates = [row.session_date for row in rows]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise RollScheduleError("OI snapshots must have unique ascending sessions")
    pair = (rows[0].front_contract_id, rows[0].next_contract_id,
            rows[0].front_expiry, rows[0].next_expiry)
    if not pair[0] or not pair[1] or pair[0] == pair[1]:
        raise RollScheduleError("front and next contract IDs must be distinct and non-empty")
    for row in rows:
        if (row.front_contract_id, row.next_contract_id, row.front_expiry, row.next_expiry) != pair:
            raise RollScheduleError("one roll schedule must cover a single contract pair")
        if type(row.front_open_interest) is not int or type(row.next_open_interest) is not int:
            raise RollScheduleError("open interest must be integer contract counts")
        if row.front_open_interest < 0 or row.next_open_interest < 0:
            raise RollScheduleError("open interest cannot be negative")
        if not row.source_id.strip() or not re.fullmatch(r"[0-9a-f]{64}", row.source_sha256 or ""):
            raise RollScheduleError("each OI snapshot requires source ID and SHA-256")
        if row.next_expiry <= row.front_expiry or row.session_date > row.next_expiry:
            raise RollScheduleError("contract expiries are invalid for the OI snapshot")

    policy = {
        "version": "oi-expiry-roll-v1",
        "confirmation_sessions": confirmation_sessions,
        "force_calendar_days_before_expiry": force_calendar_days_before_expiry,
        "comparison": "next_open_interest >= front_open_interest",
    }
    policy_sha = hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    decisions: list[RollDecision] = []
    streak = 0
    rolled = False
    for row in rows:
        if rolled:
            choice, reason = "HOLD", "ROLL_ALREADY_PLANNED_FOR_PAIR"
        elif row.session_date >= row.front_expiry:
            choice, reason = "BLOCKED", "FRONT_CONTRACT_EXPIRED_WITHOUT_ROLL"
        elif row.front_expiry.toordinal() - row.session_date.toordinal() <= force_calendar_days_before_expiry:
            choice, reason, rolled = "ROLL", "EXPIRY_BUFFER_REACHED", True
        else:
            streak = streak + 1 if row.next_open_interest >= row.front_open_interest else 0
            if streak >= confirmation_sessions:
                choice, reason, rolled = "ROLL", "NEXT_CONTRACT_OI_CONFIRMED", True
            else:
                choice = "HOLD"
                reason = "OI_CROSSOVER_CONFIRMING" if streak else "FRONT_OI_LEADS"
        decisions.append(RollDecision(
            row.session_date, choice, reason, row.front_contract_id,
            row.next_contract_id, row.source_sha256,
        ))
    body = {
        "policy_sha256": policy_sha,
        "observations": [
            {
                "session_date": row.session_date.isoformat(),
                "front_contract_id": row.front_contract_id,
                "next_contract_id": row.next_contract_id,
                "front_open_interest": row.front_open_interest,
                "next_open_interest": row.next_open_interest,
                "front_expiry": row.front_expiry.isoformat(),
                "next_expiry": row.next_expiry.isoformat(),
                "source_id": row.source_id,
                "source_sha256": row.source_sha256,
            }
            for row in rows
        ],
        "decisions": [
            {"session_date": item.session_date.isoformat(), "decision": item.decision,
             "reason": item.reason, "source_sha256": item.open_interest_source_sha256}
            for item in decisions
        ],
    }
    evidence_sha = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return RollSchedule(tuple(decisions), policy_sha, evidence_sha)
