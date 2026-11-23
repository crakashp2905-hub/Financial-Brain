"""Separate rejected-signal follow-up observations from filled-trade P&L."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime

_HASH = re.compile(r"^[0-9a-f]{64}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS rejected_signal_counterfactuals (
    signal_id VARCHAR PRIMARY KEY,
    reason_code VARCHAR NOT NULL,
    rejected_at TIMESTAMPTZ NOT NULL,
    decision_sha256 VARCHAR NOT NULL,
    record_sha256 VARCHAR NOT NULL,
    classification VARCHAR NOT NULL DEFAULT 'COUNTERFACTUAL_NOT_PNL'
);
CREATE TABLE IF NOT EXISTS counterfactual_outcomes (
    signal_id VARCHAR NOT NULL,
    horizon_sessions INTEGER NOT NULL,
    observed_on DATE NOT NULL,
    realized_return DOUBLE NOT NULL,
    benchmark_return DOUBLE,
    outcome_source_sha256 VARCHAR NOT NULL,
    record_sha256 VARCHAR NOT NULL,
    classification VARCHAR NOT NULL DEFAULT 'COUNTERFACTUAL_NOT_PNL',
    PRIMARY KEY (signal_id, horizon_sessions)
);
"""


class CounterfactualError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RejectedSignal:
    signal_id: str
    reason_code: str
    rejected_at: datetime
    decision_sha256: str

    def __post_init__(self) -> None:
        if not self.signal_id.strip() or not self.reason_code.strip():
            raise CounterfactualError("signal_id and reason_code are required")
        if self.rejected_at.tzinfo is None or self.rejected_at.utcoffset() is None:
            raise CounterfactualError("rejected_at must be timezone-aware")
        if not _HASH.fullmatch(self.decision_sha256.lower()):
            raise CounterfactualError("decision_sha256 must be SHA-256")


@dataclass(frozen=True, slots=True)
class CounterfactualOutcome:
    signal_id: str
    horizon_sessions: int
    observed_on: date
    realized_return: float
    benchmark_return: float | None
    outcome_source_sha256: str

    def __post_init__(self) -> None:
        if not self.signal_id.strip() or self.horizon_sessions < 1:
            raise CounterfactualError("signal_id and positive horizon are required")
        values = [self.realized_return]
        if self.benchmark_return is not None:
            values.append(self.benchmark_return)
        if not all(math.isfinite(x) and x > -1 for x in values):
            raise CounterfactualError("returns must be finite and greater than -100 percent")
        if not _HASH.fullmatch(self.outcome_source_sha256.lower()):
            raise CounterfactualError("outcome_source_sha256 must be SHA-256")


def ensure_schema(con) -> None:
    con.execute(SCHEMA)


def record_rejection(con, item: RejectedSignal) -> str:
    ensure_schema(con)
    body = {"signal_id": item.signal_id, "reason_code": item.reason_code,
            "rejected_at": item.rejected_at.isoformat(),
            "decision_sha256": item.decision_sha256.lower(),
            "classification": "COUNTERFACTUAL_NOT_PNL"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                              allow_nan=False).encode()).hexdigest()
    con.execute("""INSERT INTO rejected_signal_counterfactuals
        (signal_id, reason_code, rejected_at, decision_sha256, record_sha256)
        VALUES (?, ?, ?, ?, ?) ON CONFLICT (signal_id) DO NOTHING""",
        [item.signal_id, item.reason_code, item.rejected_at, item.decision_sha256.lower(), digest])
    stored = con.execute("SELECT record_sha256 FROM rejected_signal_counterfactuals WHERE signal_id=?",
                         [item.signal_id]).fetchone()
    if stored is None or stored[0] != digest:
        raise CounterfactualError("rejected signal replay conflicts with immutable record")
    return digest


def record_outcome(con, item: CounterfactualOutcome) -> str:
    ensure_schema(con)
    if con.execute("SELECT 1 FROM rejected_signal_counterfactuals WHERE signal_id=?",
                   [item.signal_id]).fetchone() is None:
        raise CounterfactualError("counterfactual outcome requires a stored rejection")
    body = {"signal_id": item.signal_id, "horizon_sessions": item.horizon_sessions,
            "observed_on": item.observed_on.isoformat(), "realized_return": item.realized_return,
            "benchmark_return": item.benchmark_return,
            "outcome_source_sha256": item.outcome_source_sha256.lower(),
            "classification": "COUNTERFACTUAL_NOT_PNL"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                              allow_nan=False).encode()).hexdigest()
    con.execute("""INSERT INTO counterfactual_outcomes
        (signal_id, horizon_sessions, observed_on, realized_return, benchmark_return,
         outcome_source_sha256, record_sha256)
        VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (signal_id, horizon_sessions) DO NOTHING""",
        [item.signal_id, item.horizon_sessions, item.observed_on, item.realized_return,
         item.benchmark_return, item.outcome_source_sha256.lower(), digest])
    stored = con.execute("""SELECT record_sha256 FROM counterfactual_outcomes
                            WHERE signal_id=? AND horizon_sessions=?""",
                         [item.signal_id, item.horizon_sessions]).fetchone()
    if stored is None or stored[0] != digest:
        raise CounterfactualError("counterfactual outcome replay conflicts with immutable record")
    return digest
