"""Append-only ledger of mechanism-first alpha hypotheses, separate from results."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


class HypothesisLedgerError(ValueError):
    """Hypothesis is incomplete, duplicated or fails ledger integrity checks."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS alpha_hypotheses (
    hypothesis_id TEXT NOT NULL,
    version TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    body TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY (hypothesis_id, version)
);
CREATE TRIGGER IF NOT EXISTS alpha_hypotheses_no_update
BEFORE UPDATE ON alpha_hypotheses BEGIN
    SELECT RAISE(ABORT, 'alpha hypothesis records are immutable');
END;
CREATE TRIGGER IF NOT EXISTS alpha_hypotheses_no_delete
BEFORE DELETE ON alpha_hypotheses BEGIN
    SELECT RAISE(ABORT, 'alpha hypothesis records are immutable');
END;
"""


@dataclass(frozen=True, slots=True)
class AlphaHypothesis:
    hypothesis_id: str
    version: str
    mechanism: str
    counterparty: str
    information_friction: str
    expected_horizon: str
    falsification_conditions: tuple[str, ...]
    source_commit: str
    source_sha256: str

    def validate(self) -> None:
        required = (self.hypothesis_id, self.version, self.mechanism, self.counterparty,
                    self.information_friction, self.expected_horizon)
        if any(not isinstance(value, str) or not value.strip() for value in required):
            raise HypothesisLedgerError("identity, mechanism, counterparty, information friction and horizon are required")
        if not self.falsification_conditions or any(not value.strip()
                                                   for value in self.falsification_conditions):
            raise HypothesisLedgerError("at least one falsification condition is required")
        if not re.fullmatch(r"[0-9a-f]{40}", self.source_commit):
            raise HypothesisLedgerError("source_commit must be a full lowercase Git SHA-1")
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise HypothesisLedgerError("source_sha256 must be a lowercase SHA-256")


def connect(path: str = "data/alpha_hypotheses.sqlite3") -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def _body(hypothesis: AlphaHypothesis) -> str:
    value = asdict(hypothesis)
    value["falsification_conditions"] = list(hypothesis.falsification_conditions)
    return json.dumps({"schema": "fb.alpha_hypothesis.v1", **value},
                      sort_keys=True, separators=(",", ":"), allow_nan=False)


def register(con: sqlite3.Connection, hypothesis: AlphaHypothesis, *,
             recorded_at: datetime | None = None) -> str:
    """Register immutable hypothesis fields before creating any result record."""
    hypothesis.validate()
    recorded_at = recorded_at or datetime.now(timezone.utc)
    if recorded_at.tzinfo is None:
        raise HypothesisLedgerError("recorded_at must be timezone-aware")
    body = _body(hypothesis)
    digest = hashlib.sha256(body.encode()).hexdigest()
    try:
        con.execute("INSERT INTO alpha_hypotheses VALUES (?, ?, ?, ?, ?)",
                    (hypothesis.hypothesis_id, hypothesis.version,
                     recorded_at.astimezone(timezone.utc).isoformat(), body, digest))
    except sqlite3.IntegrityError as exc:
        raise HypothesisLedgerError("hypothesis version is already registered") from exc
    return digest


def audit(con: sqlite3.Connection) -> dict[str, object]:
    rows = con.execute("""SELECT hypothesis_id, version, body, sha256
                         FROM alpha_hypotheses ORDER BY hypothesis_id, version""").fetchall()
    invalid = [(identity, version) for identity, version, body, digest in rows
               if hashlib.sha256(body.encode()).hexdigest() != digest]
    return {"count": len(rows), "invalid": invalid,
            "external_timestamp_attested": False,
            "result_records_included": False}
