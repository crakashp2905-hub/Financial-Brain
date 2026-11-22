"""Durable human decisions for manual-approval paper sleeves; never routes broker orders."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from financial_brain.forward_edge.approvals import is_agent_actor
from financial_brain.kernel.paper_account import PaperAccount, PaperAccountError, TraderApproval


class ManualPaperDecisionError(ValueError):
    """Invalid or conflicting human choice for a paper candidate."""


class ManualPaperAction(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    WATCH = "WATCH"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class ManualPaperDecision:
    decision_id: str
    account_id: str
    desk_id: str
    candidate_id: str
    action: ManualPaperAction
    actor_id: str
    decided_at: datetime
    reason: str
    order_sha256: str | None = None

    def __post_init__(self) -> None:
        identities = (self.decision_id, self.account_id, self.desk_id, self.candidate_id, self.actor_id)
        if any(not isinstance(value, str) or not value.strip() for value in identities):
            raise ManualPaperDecisionError("decision, account, desk, candidate and human actor are required")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", self.decision_id):
            raise ManualPaperDecisionError("decision_id must be a stable identifier")
        if is_agent_actor(self.actor_id, "human"):
            raise ManualPaperDecisionError("manual paper decisions require a named human actor")
        if not isinstance(self.action, ManualPaperAction):
            raise ManualPaperDecisionError("unknown manual paper decision")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ManualPaperDecisionError("manual paper decision needs a reason")
        if self.decided_at.tzinfo is None or self.decided_at.utcoffset() is None:
            raise ManualPaperDecisionError("decision time must be timezone-aware")
        if self.order_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", self.order_sha256):
            raise ManualPaperDecisionError("order_sha256 must be lowercase SHA-256")
        if self.action is ManualPaperAction.APPROVE and self.order_sha256 is None:
            raise ManualPaperDecisionError("APPROVE must bind the exact paper order terms")

    def body(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "account_id": self.account_id,
            "desk_id": self.desk_id,
            "candidate_id": self.candidate_id,
            "action": self.action.value,
            "actor_id": self.actor_id,
            "decided_at": self.decided_at.astimezone(timezone.utc).isoformat(),
            "reason": self.reason,
            "order_sha256": self.order_sha256,
        }

    def to_trader_approval(self, *, submitted_at: datetime) -> TraderApproval:
        """Convert only APPROVE into the existing paper-only exact-order receipt."""
        if self.action is not ManualPaperAction.APPROVE or self.order_sha256 is None:
            raise ManualPaperDecisionError(f"{self.action.value} cannot authorize a paper order")
        if submitted_at.tzinfo is None or submitted_at.utcoffset() is None:
            raise ManualPaperDecisionError("paper submission time must be timezone-aware")
        if self.decided_at.astimezone(timezone.utc) > submitted_at.astimezone(timezone.utc):
            raise ManualPaperDecisionError("decision cannot follow paper submission")
        try:
            return TraderApproval(
                approval_id=self.decision_id,
                account_id=self.account_id,
                approved_by=self.actor_id,
                approved_at=self.decided_at,
                order_sha256=self.order_sha256,
            )
        except PaperAccountError as exc:
            raise ManualPaperDecisionError(str(exc)) from exc


def paper_order_sha256(*, client_id: str, symbol: str, side: str, quantity: int,
                       limit: str | None, reserve: str) -> str:
    """Hash the exact canonical order representation consumed by ``PaperAccount.submit_intent``."""
    intent = {
        "t": "INTENT", "client_id": client_id, "symbol": symbol, "side": side.upper(),
        "quantity": quantity, "limit": limit, "reserve": reserve,
    }
    try:
        return PaperAccount.intent_sha256(intent)
    except PaperAccountError as exc:
        raise ManualPaperDecisionError(str(exc)) from exc


class ManualPaperDecisionJournal:
    """Append-only decision store; decisions cannot change execution mode or call a broker."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS manual_paper_decisions (
            decision_id TEXT PRIMARY KEY, body_sha256 TEXT NOT NULL, body_json TEXT NOT NULL)""")
        self._con.execute("""CREATE TRIGGER IF NOT EXISTS manual_paper_decisions_no_update
            BEFORE UPDATE ON manual_paper_decisions BEGIN
            SELECT RAISE(ABORT, 'manual paper decisions are append-only'); END""")
        self._con.execute("""CREATE TRIGGER IF NOT EXISTS manual_paper_decisions_no_delete
            BEFORE DELETE ON manual_paper_decisions BEGIN
            SELECT RAISE(ABORT, 'manual paper decisions are append-only'); END""")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def append(self, decision: ManualPaperDecision) -> bool:
        body = decision.body()
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        prior = self._con.execute(
            "SELECT body_sha256,body_json FROM manual_paper_decisions WHERE decision_id=?",
            [decision.decision_id],
        ).fetchone()
        if prior:
            if prior != (digest, encoded):
                raise ManualPaperDecisionError("decision ID reused with different content")
            return False
        self._con.execute("INSERT INTO manual_paper_decisions VALUES (?,?,?)",
                          [decision.decision_id, digest, encoded])
        self._con.commit()
        return True

    def decisions(self, *, account_id: str, desk_id: str) -> tuple[dict[str, Any], ...]:
        rows = self._con.execute(
            "SELECT body_json FROM manual_paper_decisions "
            "WHERE json_extract(body_json,'$.account_id')=? "
            "AND json_extract(body_json,'$.desk_id')=? ORDER BY json_extract(body_json,'$.decided_at'),decision_id",
            [account_id, desk_id],
        ).fetchall()
        return tuple(json.loads(row[0]) for row in rows)


__all__ = [
    "ManualPaperAction", "ManualPaperDecision", "ManualPaperDecisionError",
    "ManualPaperDecisionJournal", "paper_order_sha256",
]
