"""Auditable paper-operator choices and explicit live-mode lock reasons (FE-0123)."""
from __future__ import annotations

import hashlib
import html
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from financial_brain.forward_edge.approvals import is_agent_actor


class OperatorDecisionError(ValueError):
    """Operator action is invalid or conflicts with a prior journal entry."""


class OperatorAction(StrEnum):
    CONTINUE_PAPER = "CONTINUE_PAPER"
    PAUSE = "PAUSE"
    INVESTIGATE = "INVESTIGATE"
    REJECT = "REJECT"
    REQUEST_REVIEW = "REQUEST_REVIEW"


@dataclass(frozen=True)
class OperatorDecision:
    decision_id: str
    session_id: str
    actor_id: str
    actor_type: str
    action: OperatorAction
    reason: str
    decided_at: datetime
    evidence_sha256: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in
                   (self.decision_id, self.session_id, self.actor_id, self.reason)):
            raise OperatorDecisionError("decision, session, human actor and reason are required")
        if is_agent_actor(self.actor_id, self.actor_type):
            raise OperatorDecisionError("operator decisions require a named human actor")
        if not isinstance(self.action, OperatorAction):
            raise OperatorDecisionError("unknown operator action")
        if self.decided_at.tzinfo is None or self.decided_at.utcoffset() is None:
            raise OperatorDecisionError("decision timestamp must be timezone aware")
        if len(self.evidence_sha256) != 64 or any(
                char not in "0123456789abcdef" for char in self.evidence_sha256):
            raise OperatorDecisionError("evidence_sha256 must be lowercase SHA-256")

    def body(self) -> dict[str, str]:
        return {"decision_id": self.decision_id, "session_id": self.session_id,
                "actor_id": self.actor_id, "actor_type": self.actor_type,
                "action": self.action.value, "reason": self.reason,
                "decided_at": self.decided_at.astimezone(timezone.utc).isoformat(),
                "evidence_sha256": self.evidence_sha256}

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.body(), sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


class OperatorDecisionJournal:
    """Append-only journal; recording a choice cannot change an execution mode."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("CREATE TABLE IF NOT EXISTS operator_decisions (decision_id TEXT PRIMARY KEY, digest TEXT NOT NULL, body TEXT NOT NULL)")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS operator_decisions_no_update BEFORE UPDATE ON operator_decisions BEGIN SELECT RAISE(ABORT,'operator decisions are immutable'); END")
        self._con.execute("CREATE TRIGGER IF NOT EXISTS operator_decisions_no_delete BEFORE DELETE ON operator_decisions BEGIN SELECT RAISE(ABORT,'operator decisions are immutable'); END")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def append(self, decision: OperatorDecision) -> bool:
        encoded = json.dumps(decision.body(), sort_keys=True, separators=(",", ":"),
                             allow_nan=False)
        prior = self._con.execute("SELECT digest,body FROM operator_decisions WHERE decision_id=?",
                                  [decision.decision_id]).fetchone()
        if prior:
            if prior != (decision.digest(), encoded):
                raise OperatorDecisionError("decision ID reused with different content")
            return False
        self._con.execute("INSERT INTO operator_decisions VALUES (?,?,?)",
                          [decision.decision_id, decision.digest(), encoded])
        self._con.commit()
        return True

    def session_decisions(self, session_id: str) -> tuple[dict[str, str], ...]:
        rows = self._con.execute("SELECT body FROM operator_decisions WHERE json_extract(body,'$.session_id')=? ORDER BY json_extract(body,'$.decided_at'),decision_id",
                                 [session_id]).fetchall()
        return tuple(json.loads(row[0]) for row in rows)


def live_lock_reasons(audit_report: Mapping[str, object]) -> tuple[str, ...]:
    """Explain every reported blocker and retain the build-policy physical-order lock."""
    checks = audit_report.get("checks", [])
    blocking = audit_report.get("blocking", ())
    if isinstance(blocking, (list, tuple)):
        reasons = set(str(value) for value in blocking)
    elif blocking:
        reasons = {str(blocking)}
    else:
        reasons = set()
    if isinstance(checks, list):
        for check in checks:
            if isinstance(check, dict) and check.get("status") != "GREEN":
                reasons.add(f"{check.get('name', 'unknown')}: {check.get('reason', 'not green')}")
    reasons.add("physical_orders: disabled by repository build policy")
    return tuple(sorted(reasons))


def render_operator_panel(*, audit_report: Mapping[str, object],
                          decisions: tuple[dict[str, str], ...], output: Path) -> Path:
    """Render the five human choices and why LIVE_CASH remains locked."""
    reasons = live_lock_reasons(audit_report)
    lock_list = "".join("<li>" + html.escape(reason) + "</li>" for reason in reasons)
    if not decisions:
        decision_rows = "<tr><td colspan='5'>No human operator decision recorded</td></tr>"
    else:
        decision_rows = "".join(
            "<tr>" + "".join("<td>" + html.escape(row[key]) + "</td>"
                              for key in ("decided_at", "actor_id", "action", "reason",
                                          "evidence_sha256")) + "</tr>"
            for row in decisions)
    choices = "".join("<li>" + html.escape(action.value.replace("_", " ")) + "</li>"
                     for action in OperatorAction)
    page = ("<!doctype html><html lang='en'><meta charset='utf-8'><title>Operator decisions</title>"
            "<style>body{font:14px system-ui;max-width:900px;margin:2rem auto;padding:0 1rem}"
            "section{border:1px solid #ddd;border-radius:6px;padding:1rem;margin:1rem 0}"
            "table{border-collapse:collapse;width:100%}td,th{padding:.5rem;border-bottom:1px solid #ddd;text-align:left}"
            ".locked{color:#9a2b26;font-weight:700}</style><main><h1>Paper operator decisions</h1>"
            "<p>Choices are audited human records. They do not enable live mode, submit orders or change the current session mode.</p>"
            "<section><h2>Available decisions</h2><ul>" + choices + "</ul></section>"
            "<section><h2 class='locked'>LIVE_CASH LOCKED</h2><p>Reasons:</p><ul>" + lock_list
            + "</ul></section><section><h2>Decision history</h2><div style='overflow:auto'><table><thead><tr>"
            "<th>Time</th><th>Human</th><th>Decision</th><th>Reason</th><th>Evidence SHA-256</th></tr></thead><tbody>"
            + decision_rows + "</tbody></table></div></section></main></html>")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf-8")
    return destination
