"""Keep autonomous and human-approved paper sleeves as separate books."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Sleeve(StrEnum):
    AUTONOMOUS = "AUTONOMOUS"
    MANUAL_APPROVED = "MANUAL_APPROVED"


@dataclass(frozen=True, slots=True)
class HumanApproval:
    decision_id: str
    actor_id: str
    approved_at: datetime

    def __post_init__(self) -> None:
        if not self.decision_id.strip() or not self.actor_id.startswith("human:"):
            raise ValueError("manual sleeve requires an identified human approval")
        if self.approved_at.tzinfo is None or self.approved_at.utcoffset() is None:
            raise ValueError("approval timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class SleeveAssignment:
    assignment_id: str
    signal_id: str
    sleeve: Sleeve
    book_id: str
    signal_sha256: str
    assigned_at: datetime
    approval_id: str | None


SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_sleeve_assignments (
    assignment_id VARCHAR NOT NULL UNIQUE,
    signal_id VARCHAR NOT NULL,
    sleeve VARCHAR NOT NULL,
    book_id VARCHAR NOT NULL,
    signal_sha256 VARCHAR NOT NULL,
    assigned_at TIMESTAMPTZ NOT NULL,
    approval_id VARCHAR,
    record_sha256 VARCHAR NOT NULL,
    PRIMARY KEY (signal_id, sleeve),
    UNIQUE (signal_id, book_id)
);
"""


def route_to_sleeves(*, signal_id: str, signal_sha256: str, assigned_at: datetime,
                     autonomous_eligible: bool, approval: HumanApproval | None = None) -> tuple[SleeveAssignment, ...]:
    """A human choice can add a separate manual assignment; it never mutates auto eligibility."""
    if not signal_id.strip() or len(signal_sha256) != 64:
        raise ValueError("signal identity and SHA-256 are required")
    if assigned_at.tzinfo is None or assigned_at.utcoffset() is None:
        raise ValueError("assignment timestamp must be timezone-aware")
    assignments = []
    if autonomous_eligible:
        assignments.append(_assignment(signal_id, signal_sha256, assigned_at,
                                       Sleeve.AUTONOMOUS, "paper:auto", None))
    if approval is not None:
        if approval.approved_at > assigned_at:
            raise ValueError("manual approval cannot occur after paper assignment")
        assignments.append(_assignment(signal_id, signal_sha256, assigned_at,
                                       Sleeve.MANUAL_APPROVED,
                                       f"paper:manual:{approval.decision_id}", approval.decision_id))
    if not assignments:
        return ()
    if len({row.book_id for row in assignments}) != len(assignments):
        raise ValueError("sleeve book identifiers must be distinct")
    return tuple(assignments)


def _assignment(signal_id: str, signal_hash: str, assigned_at: datetime,
                sleeve: Sleeve, book_id: str, approval_id: str | None) -> SleeveAssignment:
    payload = {"signal_id": signal_id, "signal_sha256": signal_hash.lower(),
               "assigned_at": assigned_at.isoformat(), "sleeve": sleeve.value,
               "book_id": book_id, "approval_id": approval_id}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                              allow_nan=False).encode()).hexdigest()
    return SleeveAssignment(digest, signal_id, sleeve, book_id, signal_hash.lower(),
                            assigned_at, approval_id)


def persist_assignments(con, assignments: tuple[SleeveAssignment, ...]) -> int:
    """Append immutable sleeve routing evidence; conflicting retries fail closed."""
    con.execute(SCHEMA)
    inserted = 0
    for item in assignments:
        body = {"assignment_id": item.assignment_id, "signal_id": item.signal_id,
                "sleeve": item.sleeve.value, "book_id": item.book_id,
                "signal_sha256": item.signal_sha256, "assigned_at": item.assigned_at.isoformat(),
                "approval_id": item.approval_id}
        record_hash = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                       allow_nan=False).encode()).hexdigest()
        con.execute("""INSERT INTO paper_sleeve_assignments
            (assignment_id, signal_id, sleeve, book_id, signal_sha256, assigned_at, approval_id,
             record_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (signal_id, sleeve) DO NOTHING""",
            [item.assignment_id, item.signal_id, item.sleeve.value, item.book_id,
             item.signal_sha256, item.assigned_at, item.approval_id, record_hash])
        stored = con.execute("""SELECT assignment_id, book_id, signal_sha256, assigned_at,
                                      approval_id, record_sha256 FROM paper_sleeve_assignments
                               WHERE signal_id=? AND sleeve=?""",
                             [item.signal_id, item.sleeve.value]).fetchone()
        expected = (item.assignment_id, item.book_id, item.signal_sha256,
                    item.assigned_at, item.approval_id, record_hash)
        if stored != expected:
            raise ValueError("paper sleeve assignment conflicts with an immutable prior record")
        inserted += 1
    return inserted
