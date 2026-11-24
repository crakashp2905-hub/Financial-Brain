"""FE-0068 outage, stale data and missing inputs become explicit no-trade records."""
from datetime import datetime, timezone

import pytest

from financial_brain.forward_edge.decision_journal import DecisionJournal
from financial_brain.forward_edge.outage_gate import evaluate_outage, journal_no_trade


def test_live_complete_inputs_can_continue_but_stale_and_down_are_no_trade() -> None:
    live = evaluate_outage(quote_age_seconds=1, stale_after_seconds=2, down_after_seconds=5,
                           required_inputs={"quote": True}, broker_state_known=True)
    assert live.decision == "EVALUATE" and live.feed_status == "LIVE"
    stale = evaluate_outage(quote_age_seconds=3, stale_after_seconds=2, down_after_seconds=5,
                            required_inputs={"quote": True}, broker_state_known=True)
    assert stale.decision == "NO_TRADE" and stale.reason_codes == ("QUOTE_STALE",)
    down = evaluate_outage(quote_age_seconds=8, stale_after_seconds=2, down_after_seconds=5,
                           required_inputs={"quote": True}, broker_state_known=True)
    assert down.reason_codes == ("MARKET_DATA_OUTAGE",)


def test_missing_inputs_and_unknown_broker_state_are_journaled_without_fallback(tmp_path) -> None:
    decision = evaluate_outage(quote_age_seconds=1, stale_after_seconds=2, down_after_seconds=5,
                               required_inputs={"quote": None, "calendar": "nse"}, broker_state_known=False)
    journal = DecisionJournal(tmp_path / "decisions.sqlite3")
    try:
        assert journal_no_trade(journal=journal, session_id="s1", strategy_id="orb", decision_id="d1",
                                decided_at=datetime(2026, 11, 15, tzinfo=timezone.utc),
                                input_sha256="a" * 64, decision=decision)
        row = journal.session_records("s1")[0]
        assert row["outcome"] == "NO_SIGNAL"
        assert row["payload"]["fallback_fill"] is False
        assert "BROKER_STATE_UNKNOWN" in row["reason_codes"]
        assert "INPUT_MISSING:quote" in row["reason_codes"]
    finally:
        journal.close()


def test_no_trade_writer_refuses_evaluate_result() -> None:
    decision = evaluate_outage(quote_age_seconds=1, stale_after_seconds=2, down_after_seconds=5,
                               required_inputs={"quote": True}, broker_state_known=True)
    journal = DecisionJournal(":memory:")
    try:
        with pytest.raises(ValueError, match="NO_TRADE"):
            journal_no_trade(journal=journal, session_id="s", strategy_id="orb", decision_id="d",
                             decided_at=datetime.now(timezone.utc), input_sha256="a" * 64,
                             decision=decision)
    finally:
        journal.close()
