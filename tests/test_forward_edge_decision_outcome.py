from datetime import datetime, timezone
import hashlib

import pytest

from financial_brain.forward_edge.data_gate import DataGateResult, GateReason
from financial_brain.forward_edge.decision_journal import DecisionJournal
from financial_brain.forward_edge.decision_outcome import (
    ForwardDecision,
    evaluate_forward_decision,
    journal_abstention,
)


def _allowed_market_data():
    return DataGateResult("TRADE", ())


def _evaluate(**overrides):
    values = {
        "market_data": _allowed_market_data(),
        "requested_notional": 10_000.0,
        "visible_liquidity_notional": 12_000.0,
        "expected_gross_alpha_bps": 30.0,
        "estimated_all_in_cost_bps": 10.0,
        "margin_is_certain": True,
        "risk_approved": True,
    }
    values.update(overrides)
    return evaluate_forward_decision(**values)


def test_every_gate_passes_only_with_positive_after_cost_alpha():
    decision = _evaluate()
    assert decision.decision == "TRADE"
    assert decision.after_cost_alpha_bps == 20
    assert decision.reason_codes == ()


@pytest.mark.parametrize(("changes", "reason"), [
    ({"market_data": DataGateResult("NO_TRADE", (GateReason.QUOTE_STALE,))}, "QUOTE_STALE"),
    ({"visible_liquidity_notional": 9_000.0}, "INSUFFICIENT_LIQUIDITY"),
    ({"visible_liquidity_notional": None}, "LIQUIDITY_UNKNOWN"),
    ({"margin_is_certain": False}, "MARGIN_UNCERTAIN"),
    ({"risk_approved": False}, "RISK_NOT_APPROVED"),
    ({"expected_gross_alpha_bps": 10.0}, "NON_POSITIVE_AFTER_COST_ALPHA"),
    ({"expected_gross_alpha_bps": None}, "ALPHA_UNKNOWN"),
    ({"estimated_all_in_cost_bps": None}, "COST_UNKNOWN"),
])
def test_blocking_conditions_are_explicit_no_trade(changes, reason):
    result = _evaluate(**changes)
    assert result.decision == "NO_TRADE"
    assert reason in result.reason_codes


def test_abstention_is_journaled_idempotently_without_fallback_fill(tmp_path):
    decision = _evaluate(market_data=DataGateResult("NO_TRADE", (GateReason.QUOTE_STALE,)))
    journal = DecisionJournal(tmp_path / "decisions.sqlite3")
    timestamp = datetime(2026, 11, 16, 4, tzinfo=timezone.utc)
    digest = hashlib.sha256(b"inputs").hexdigest()
    assert journal_abstention(journal=journal, session_id="session-1", strategy_id="swing-v1",
                              decision_id="decision-1", decided_at=timestamp,
                              input_sha256=digest, decision=decision)
    assert not journal_abstention(journal=journal, session_id="session-1", strategy_id="swing-v1",
                                  decision_id="decision-1", decided_at=timestamp,
                                  input_sha256=digest, decision=decision)
    row = journal.session_records("session-1")[0]
    assert row["outcome"] == "NO_SIGNAL"
    assert row["payload"]["decision"] == "NO_TRADE"
    assert row["payload"]["fallback_fill"] is False
    assert "QUOTE_STALE" in row["reason_codes"]
    journal.close()


def test_abstention_writer_rejects_trade_decision(tmp_path):
    journal = DecisionJournal(tmp_path / "decisions.sqlite3")
    decision = ForwardDecision("TRADE", (), 100, 1)
    with pytest.raises(ValueError, match="reasoned NO_TRADE"):
        journal_abstention(journal=journal, session_id="s", strategy_id="x", decision_id="d",
                           decided_at=datetime(2026, 11, 16, tzinfo=timezone.utc),
                           input_sha256=hashlib.sha256(b"x").hexdigest(), decision=decision)
    journal.close()
