"""Explicit no-trade outcomes for outages, stale quotes and missing inputs (FE-0068)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from financial_brain.forward_edge.decision_journal import DecisionJournal, DecisionRecord
from financial_brain.kernel.closures_n import DataStatus, status_of


@dataclass(frozen=True)
class OutageDecision:
    decision: str
    reason_codes: tuple[str, ...]
    feed_status: str


def evaluate_outage(*, quote_age_seconds: float, stale_after_seconds: float,
                    down_after_seconds: float, required_inputs: dict[str, Any],
                    broker_state_known: bool) -> OutageDecision:
    status = status_of(quote_age_seconds, stale_after_seconds, down_after_seconds)
    reasons: set[str] = set()
    if status == DataStatus.STALE:
        reasons.add("QUOTE_STALE")
    elif status == DataStatus.DOWN:
        reasons.add("MARKET_DATA_OUTAGE")
    if not broker_state_known:
        reasons.add("BROKER_STATE_UNKNOWN")
    reasons.update(f"INPUT_MISSING:{name}" for name, value in required_inputs.items() if value is None)
    if not required_inputs:
        reasons.add("REQUIRED_INPUTS_UNDECLARED")
    return OutageDecision("NO_TRADE" if reasons else "EVALUATE", tuple(sorted(reasons)), status.value)


def journal_no_trade(*, journal: DecisionJournal, session_id: str, strategy_id: str,
                     decision_id: str, decided_at: datetime, input_sha256: str,
                     decision: OutageDecision) -> bool:
    """Persist an outage abstention as an explicit positive outcome."""
    if decision.decision != "NO_TRADE" or not decision.reason_codes:
        raise ValueError("only a reasoned NO_TRADE decision can be journaled here")
    record = DecisionRecord(session_id, strategy_id, decision_id, "NO_SIGNAL", decided_at,
                            decision.reason_codes, input_sha256,
                            {"feed_status": decision.feed_status, "fallback_fill": False})
    return journal.append(record)
