"""Immutable as-of context records for feasible paper counterfactuals."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from .inverse_strategy import ACTIONS


def record_context(con, *, decision_ts: datetime, state_asof: datetime, instrument: str,
                   action: str, state: dict, constraints: dict,
                   alternatives: list[dict], provenance: str) -> str:
    if decision_ts.tzinfo is None or state_asof.tzinfo is None or state_asof > decision_ts:
        raise ValueError("as-of state must not be later than timezone-aware decision")
    if not instrument or action not in ACTIONS or not provenance:
        raise ValueError("instrument, recognized action, and provenance are required")
    for candidate in alternatives:
        if not {"instrument", "feasible_at_decision", "reason"} <= set(candidate):
            raise ValueError("alternative requires identity, feasibility, and reason")
    body = {"decision_ts": decision_ts.isoformat(), "state_asof": state_asof.isoformat(),
            "instrument": instrument, "action": action, "state": state,
            "constraints": constraints, "alternatives": alternatives, "provenance": provenance}
    encoded = json.dumps(body, sort_keys=True, allow_nan=False, separators=(",", ":"))
    context_id = hashlib.sha256(encoded.encode()).hexdigest()
    con.execute("""INSERT INTO inverse_decision_context VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [context_id, decision_ts, state_asof, instrument, action,
                 json.dumps(state, sort_keys=True), json.dumps(constraints, sort_keys=True),
                 json.dumps(alternatives, sort_keys=True), provenance, datetime.now(timezone.utc)])
    return context_id


def get_context(con, context_id: str) -> dict:
    row = con.execute("""SELECT decision_ts, state_asof, instrument, action, state_json,
                              constraints_json, alternatives_json, provenance
                       FROM inverse_decision_context WHERE context_id = ?""", [context_id]).fetchone()
    if row is None:
        raise KeyError(context_id)
    return {"decision_ts": row[0], "state_asof": row[1], "instrument": row[2], "action": row[3],
            "state": json.loads(row[4]), "constraints": json.loads(row[5]),
            "alternatives": json.loads(row[6]), "provenance": row[7]}


def record_rebalance_context(con, *, run_id: str, signal_session: date, execution_session: date,
                             candidates: list[dict], constraints: dict, portfolio: dict) -> str:
    """Idempotent same-content write; changed historical context is rejected."""
    if not run_id or signal_session >= execution_session:
        raise ValueError("run id and strictly earlier signal session required")
    if any(not {"lineage", "rank", "score", "filtered"} <= set(item) for item in candidates):
        raise ValueError("candidate lacks contemporaneous ranking fields")
    if set(portfolio) != {"known_before_execution", "realized_after_execution"}:
        raise ValueError("portfolio must separate known-before and realized-after state")
    content = {"run_id": run_id, "signal_session": signal_session.isoformat(),
               "execution_session": execution_session.isoformat(), "candidates": candidates,
               "constraints": constraints, "portfolio": portfolio}
    encoded = json.dumps(content, sort_keys=True, allow_nan=False, default=str)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    prior = con.execute("""SELECT content_hash FROM paper_rebalance_context
                           WHERE run_id = ? AND execution_session = ?""",
                        [run_id, execution_session]).fetchone()
    if prior is not None:
        if prior[0] != digest:
            raise ValueError("historical rebalance context changed; original snapshot is immutable")
        return digest
    con.execute("""INSERT INTO paper_rebalance_context VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [run_id, execution_session, signal_session, digest,
                 json.dumps(candidates, sort_keys=True, default=str),
                 json.dumps(constraints, sort_keys=True, default=str),
                 json.dumps(portfolio, sort_keys=True, default=str), datetime.now(timezone.utc)])
    return digest
