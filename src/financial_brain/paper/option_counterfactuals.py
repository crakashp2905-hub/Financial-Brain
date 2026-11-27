"""Rights-gated append-only ledger for rejected option candidates and counterfactuals."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime

from ..costs.india import CostModel
from ..ingest.options_quotes import licensed
from .options import OptionPaperError, Quote, long_round_trip


_SHA = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS option_candidate_events (
    event_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('REJECTED', 'COUNTERFACTUAL_RESOLVED')),
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS option_candidate_events_no_update
BEFORE UPDATE ON option_candidate_events BEGIN SELECT RAISE(ABORT, 'append-only candidate ledger'); END;
CREATE TRIGGER IF NOT EXISTS option_candidate_events_no_delete
BEFORE DELETE ON option_candidate_events BEGIN SELECT RAISE(ABORT, 'append-only candidate ledger'); END;
"""


def initialize(con: sqlite3.Connection) -> None:
    con.executescript(_SCHEMA)


def _iso(value: datetime, name: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OptionPaperError(f"{name} must be timezone-aware")
    return value.isoformat()


def _append(con: sqlite3.Connection, *, event_id: str, candidate_id: str,
            event_type: str, occurred_at: datetime, payload: dict[str, object]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, default=str)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    try:
        con.execute("INSERT INTO option_candidate_events VALUES (?, ?, ?, ?, ?, ?)",
                    (event_id, candidate_id, event_type, _iso(occurred_at, "occurred_at"), body, digest))
        con.commit()
    except sqlite3.IntegrityError as exc:
        raise OptionPaperError("candidate event already exists; ledger is append-only") from exc
    return digest


def record_rejected_candidate(con: sqlite3.Connection, *, candidate_id: str,
                              decision_at: datetime, planned_exit_at: datetime,
                              rejection_reasons: tuple[str, ...], best_quote: Quote,
                              quote_source_sha256: str, rights_reference: str,
                              event_id: str) -> str:
    """Store a rejected candidate only when quote-retention permission is enabled."""
    if not licensed():
        raise OptionPaperError("option quote archive rights are not approved")
    _iso(decision_at, "decision_at")
    _iso(planned_exit_at, "planned_exit_at")
    if (not candidate_id.strip() or not event_id.strip() or not rejection_reasons
            or not all(reason.strip() for reason in rejection_reasons)
            or not rights_reference.strip() or not _SHA.fullmatch(quote_source_sha256)):
        raise OptionPaperError("candidate, rejection, rights reference and source hash are required")
    best_quote.validate()
    if best_quote.observed_at > decision_at or planned_exit_at <= decision_at:
        raise OptionPaperError("decision quote must be known then and planned horizon must follow decision")
    initialize(con)
    payload = {"candidate_id": candidate_id, "decision_at": _iso(decision_at, "decision_at"),
               "planned_exit_at": _iso(planned_exit_at, "planned_exit_at"),
               "rejection_reasons": sorted(set(rejection_reasons)),
               "best_observed_quote": {"token": best_quote.token, "symbol": best_quote.symbol,
                   "expiry": best_quote.expiry.isoformat(), "lot_size": best_quote.lot_size,
                   "observed_at": _iso(best_quote.observed_at, "quote observed_at"),
                   "bid": best_quote.bid, "ask": best_quote.ask,
                   "bid_qty": best_quote.bid_qty, "ask_qty": best_quote.ask_qty},
               "quote_source_sha256": quote_source_sha256,
               "rights_reference": rights_reference,
               "rights_gate": "GRANTED_BY_ARCHIVE_CONFIGURATION",
               "traded": False}
    return _append(con, event_id=event_id, candidate_id=candidate_id,
                   event_type="REJECTED", occurred_at=decision_at, payload=payload)


def resolve_counterfactual(con: sqlite3.Connection, *, candidate_id: str,
                           exit_at: datetime, exit_quote: Quote | None,
                           event_id: str, lots: int = 1,
                           costs: CostModel | None = None) -> str:
    """Append one after-horizon observed-quote resolution; never fill missing outcomes."""
    if not licensed():
        raise OptionPaperError("option quote archive rights are not approved")
    row = con.execute("""SELECT payload_json FROM option_candidate_events
                         WHERE candidate_id=? AND event_type='REJECTED'""",
                      (candidate_id,)).fetchone()
    if row is None:
        raise OptionPaperError("rejected candidate must be recorded before counterfactual resolution")
    decision = json.loads(row[0])
    _iso(exit_at, "exit_at")
    if exit_at < datetime.fromisoformat(decision["planned_exit_at"]):
        raise OptionPaperError("counterfactual cannot resolve before the predeclared horizon")
    if con.execute("""SELECT 1 FROM option_candidate_events
                      WHERE candidate_id=? AND event_type='COUNTERFACTUAL_RESOLVED'""",
                   (candidate_id,)).fetchone():
        raise OptionPaperError("counterfactual outcome already resolved")
    quote_result = None
    status = "NO_EXIT_QUOTE"
    if exit_quote is not None:
        exit_quote.validate()
        entry = decision["best_observed_quote"]
        if (exit_quote.token, exit_quote.symbol, exit_quote.expiry, exit_quote.lot_size) != (
                entry["token"], entry["symbol"], date.fromisoformat(entry["expiry"]),
                entry["lot_size"]):
            raise OptionPaperError("counterfactual quote differs from rejected contract")
        entry_quote = Quote(entry["token"], entry["symbol"], date.fromisoformat(entry["expiry"]),
            entry["lot_size"], datetime.fromisoformat(entry["observed_at"]),
            entry["bid"], entry["ask"], entry["bid_qty"], entry["ask_qty"])
        if exit_quote.observed_at > exit_at:
            raise OptionPaperError("counterfactual exit quote is future-dated")
        result = long_round_trip(entry_quote, exit_quote, lots=lots, costs=costs)
        quote_result = result
        status = "RESOLVED_OBSERVED_QUOTES"
    payload = {"candidate_id": candidate_id, "exit_at": _iso(exit_at, "exit_at"),
               "status": status, "counterfactual_trade": False,
               "outcome": quote_result}
    return _append(con, event_id=event_id, candidate_id=candidate_id,
                   event_type="COUNTERFACTUAL_RESOLVED", occurred_at=exit_at, payload=payload)


def audit(con: sqlite3.Connection) -> dict[str, object]:
    rows = con.execute("SELECT event_id, candidate_id, event_type, payload_json, payload_sha256 "
                        "FROM option_candidate_events ORDER BY occurred_at, event_id").fetchall()
    invalid = []
    for event_id, _candidate, _kind, body, digest in rows:
        if hashlib.sha256(body.encode("utf-8")).hexdigest() != digest:
            invalid.append(event_id)
    return {"events": len(rows), "invalid_events": invalid,
            "rejected_candidates": sum(row[2] == "REJECTED" for row in rows),
            "counterfactual_resolutions": sum(row[2] == "COUNTERFACTUAL_RESOLVED" for row in rows),
            "rights_gate_required": True, "physical_trade_created": False}
