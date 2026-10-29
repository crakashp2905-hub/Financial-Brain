"""Append-only local evidence ledger for prospective long-option paper intents.

Local timestamps and hashes make edits detectable inside this ledger, but they
are not independent timestamp attestations. No broker order path exists here.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from .options import OptionPaperError
from .options_runner import Intent, archive_quotes, run_intent

PATH = Path("data/options_paper_forward.duckdb")
SCHEMA = """
CREATE TABLE IF NOT EXISTS intents (
    cohort VARCHAR NOT NULL, rule_version VARCHAR NOT NULL, signal_id VARCHAR NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL, decided_at TIMESTAMPTZ NOT NULL,
    planned_exit_at TIMESTAMPTZ NOT NULL, token BIGINT NOT NULL,
    payload JSON NOT NULL, payload_sha256 VARCHAR NOT NULL,
    PRIMARY KEY (cohort, rule_version, signal_id));
CREATE TABLE IF NOT EXISTS outcomes (
    cohort VARCHAR NOT NULL, rule_version VARCHAR NOT NULL, signal_id VARCHAR NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL, intent_sha256 VARCHAR NOT NULL,
    result JSON NOT NULL, result_sha256 VARCHAR NOT NULL,
    PRIMARY KEY (cohort, rule_version, signal_id));
"""


def connect(path: Path = PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    return con


def _payload(intent: Intent) -> str:
    return json.dumps(asdict(intent), sort_keys=True, default=str, separators=(",", ":"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def record_intent(con, intent: Intent, *, now: datetime | None = None) -> str:
    """Accept only a newly decided intent, before its possible paper entry."""
    intent.validate()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or not intent.decided_at <= now <= intent.decided_at + intent.max_entry_delay:
        raise OptionPaperError("intent must be recorded after decision and before its entry window closes")
    body = _payload(intent)
    digest = _digest(body)
    key = [intent.cohort, intent.rule_version, intent.signal_id]
    if con.execute("SELECT 1 FROM intents WHERE cohort=? AND rule_version=? AND signal_id=?", key).fetchone():
        raise OptionPaperError("intent identity already exists; amendments require a new signal id")
    con.execute("""INSERT INTO intents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [*key, now, intent.decided_at, intent.planned_exit_at, intent.token, body, digest])
    return digest


def record_outcome(con, quote_con, intent: Intent, *, now: datetime | None = None) -> dict:
    """Resolve once after the predeclared exit window, preserving missing fills."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now <= intent.planned_exit_at + intent.max_exit_delay:
        raise OptionPaperError("outcome cannot be recorded before the exit window closes")
    key = [intent.cohort, intent.rule_version, intent.signal_id]
    row = con.execute("""SELECT payload_sha256, payload FROM intents
                         WHERE cohort=? AND rule_version=? AND signal_id=?""", key).fetchone()
    if row is None or row[0] != _digest(_payload(intent)) or row[0] != _digest(str(row[1])):
        raise OptionPaperError("registered intent is missing or has changed")
    if con.execute("SELECT 1 FROM outcomes WHERE cohort=? AND rule_version=? AND signal_id=?", key).fetchone():
        raise OptionPaperError("outcome already recorded")
    result = run_intent(intent, archive_quotes(quote_con, intent))
    # A locally recorded intent is prospective only if the local write preceded
    # the observed entry quote. This still needs an external clock attestation.
    recorded_at = con.execute("""SELECT recorded_at FROM intents
                                WHERE cohort=? AND rule_version=? AND signal_id=?""", key).fetchone()[0]
    if result.get("entry_at") is not None and result["entry_at"] <= recorded_at:
        raise OptionPaperError("entry quote did not follow the recorded intent")
    body = json.dumps(result, sort_keys=True, default=str, separators=(",", ":"))
    con.execute("INSERT INTO outcomes VALUES (?, ?, ?, ?, ?, ?, ?)",
                [*key, now, row[0], body, _digest(body)])
    return result


def audit(con) -> dict:
    """Check local payload integrity and missing outcomes, never claim external proof."""
    intents = con.execute("SELECT cohort, rule_version, signal_id, payload, payload_sha256 FROM intents").fetchall()
    outcomes = con.execute("SELECT cohort, rule_version, signal_id, result, result_sha256 FROM outcomes").fetchall()
    bad_intents = [r[:3] for r in intents if _digest(str(r[3])) != r[4]]
    bad_outcomes = [r[:3] for r in outcomes if _digest(str(r[3])) != r[4]]
    closed = {r[:3] for r in outcomes}
    return {"intents": len(intents), "outcomes": len(outcomes),
            "pending": len(intents) - len(closed), "invalid_intents": bad_intents,
            "invalid_outcomes": bad_outcomes, "external_timestamp_attested": False}
