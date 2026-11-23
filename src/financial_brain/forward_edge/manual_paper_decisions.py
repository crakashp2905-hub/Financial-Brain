"""Human decisions for paper sleeves; approvals are scoped to one frozen candidate only."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from financial_brain.forward_edge.approvals import is_agent_actor

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ManualPaperDecisionError(ValueError):
    """A human paper decision is invalid, stale, expired or conflicts with the journal."""


class ManualPaperAction(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    WATCH = "WATCH"
    NO_TRADE = "NO_TRADE"


class ManualPaperState(StrEnum):
    APPROVED_PAPER_ONLY = "APPROVED_PAPER_ONLY"
    REJECTED = "REJECTED"
    WATCH = "WATCH"
    NO_TRADE = "NO_TRADE"
    STALE = "STALE"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class ManualPaperDecision:
    decision_id: str
    session_id: str
    candidate_id: str
    candidate_sha256: str
    actor_id: str
    actor_type: str
    action: ManualPaperAction
    reason: str
    decided_at: datetime
    expires_at: datetime
    candidate_expires_at: datetime

    def __post_init__(self) -> None:
        if not all(value.strip() for value in
                   (self.decision_id, self.session_id, self.candidate_id, self.actor_id, self.reason)):
            raise ManualPaperDecisionError("decision, session, candidate, named human and reason are required")
        if is_agent_actor(self.actor_id, self.actor_type):
            raise ManualPaperDecisionError("manual paper choices require a named human actor")
        if not isinstance(self.action, ManualPaperAction):
            raise ManualPaperDecisionError("unsupported manual paper action")
        if not _SHA256.fullmatch(self.candidate_sha256):
            raise ManualPaperDecisionError("candidate_sha256 must be lowercase SHA-256")
        for name in ("decided_at", "expires_at", "candidate_expires_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ManualPaperDecisionError(f"{name} must be timezone aware")
        if self.expires_at.astimezone(timezone.utc) <= self.decided_at.astimezone(timezone.utc):
            raise ManualPaperDecisionError("decision expiry must follow decision time")
        if self.expires_at.astimezone(timezone.utc) > self.candidate_expires_at.astimezone(timezone.utc):
            raise ManualPaperDecisionError("decision cannot outlive its candidate")

    def body(self) -> dict[str, str | bool]:
        return {
            "decision_id": self.decision_id, "session_id": self.session_id,
            "candidate_id": self.candidate_id, "candidate_sha256": self.candidate_sha256,
            "actor_id": self.actor_id, "actor_type": self.actor_type,
            "action": self.action.value, "reason": self.reason,
            "decided_at": self.decided_at.astimezone(timezone.utc).isoformat(),
            "expires_at": self.expires_at.astimezone(timezone.utc).isoformat(),
            "candidate_expires_at": self.candidate_expires_at.astimezone(timezone.utc).isoformat(),
            "execution_scope": "PAPER_ONLY", "physical_orders_authorized": False,
        }

    def digest(self) -> str:
        canonical = json.dumps(self.body(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def resolve(self, *, now: datetime, current_candidate_sha256: str,
                candidate_paper_eligible: bool) -> ManualPaperState:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ManualPaperDecisionError("resolution time must be timezone aware")
        current = now.astimezone(timezone.utc)
        if current >= min(self.expires_at.astimezone(timezone.utc),
                          self.candidate_expires_at.astimezone(timezone.utc)):
            return ManualPaperState.EXPIRED
        if current < self.decided_at.astimezone(timezone.utc):
            raise ManualPaperDecisionError("decision is not valid yet")
        if current_candidate_sha256 != self.candidate_sha256:
            return ManualPaperState.STALE
        if self.action is ManualPaperAction.APPROVE:
            if not candidate_paper_eligible:
                raise ManualPaperDecisionError("candidate is not eligible for a paper sleeve")
            return ManualPaperState.APPROVED_PAPER_ONLY
        if self.action is ManualPaperAction.REJECT:
            return ManualPaperState.REJECTED
        return ManualPaperState(self.action.value)


class ManualPaperDecisionJournal:
    """Durable, append-only journal. It records consent; it cannot submit broker orders."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS manual_paper_decisions "
            "(decision_id TEXT PRIMARY KEY, digest TEXT NOT NULL, body TEXT NOT NULL)"
        )
        self._con.execute(
            "CREATE TRIGGER IF NOT EXISTS manual_paper_no_update BEFORE UPDATE ON manual_paper_decisions "
            "BEGIN SELECT RAISE(ABORT,'manual paper decisions are immutable'); END"
        )
        self._con.execute(
            "CREATE TRIGGER IF NOT EXISTS manual_paper_no_delete BEFORE DELETE ON manual_paper_decisions "
            "BEGIN SELECT RAISE(ABORT,'manual paper decisions are immutable'); END"
        )
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def append(self, decision: ManualPaperDecision) -> bool:
        body = json.dumps(decision.body(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        previous = self._con.execute(
            "SELECT digest,body FROM manual_paper_decisions WHERE decision_id=?", [decision.decision_id]
        ).fetchone()
        if previous:
            if previous != (decision.digest(), body):
                raise ManualPaperDecisionError("decision ID reused with different content")
            return False
        self._con.execute("INSERT INTO manual_paper_decisions VALUES (?,?,?)",
                          [decision.decision_id, decision.digest(), body])
        self._con.commit()
        return True

    def session_decisions(self, session_id: str) -> tuple[dict[str, str | bool], ...]:
        rows = self._con.execute(
            "SELECT body FROM manual_paper_decisions WHERE json_extract(body,'$.session_id')=? "
            "ORDER BY json_extract(body,'$.decided_at'),decision_id", [session_id]
        ).fetchall()
        return tuple(json.loads(row[0]) for row in rows)


__all__ = ["ManualPaperAction", "ManualPaperDecision", "ManualPaperDecisionError",
           "ManualPaperDecisionJournal", "ManualPaperState"]
