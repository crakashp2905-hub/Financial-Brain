from datetime import date, datetime, timezone

import duckdb
import pytest

from financial_brain.signals.counterfactual import (CounterfactualError,
                                                     CounterfactualOutcome,
                                                     RejectedSignal, ensure_schema,
                                                     record_outcome, record_rejection)

NOW = datetime(2026, 11, 23, 10, tzinfo=timezone.utc)
HASH = "b" * 64


def test_rejected_signal_followup_is_separate_and_idempotent_not_trade_pnl():
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    rejected = RejectedSignal("s1", "RISK_GATE", NOW, HASH)
    assert record_rejection(con, rejected) == record_rejection(con, rejected)
    outcome = CounterfactualOutcome("s1", 5, date(2026, 11, 23), 0.02, 0.01, HASH)
    assert record_outcome(con, outcome) == record_outcome(con, outcome)
    rows = con.execute("""SELECT classification, realized_return, benchmark_return
                           FROM counterfactual_outcomes""").fetchall()
    assert rows == [("COUNTERFACTUAL_NOT_PNL", 0.02, 0.01)]
    columns = {row[1] for row in con.execute("PRAGMA table_info('counterfactual_outcomes')").fetchall()}
    assert "pnl" not in {item.lower() for item in columns}
    con.close()


def test_counterfactual_requires_rejection_and_immutable_replay():
    con = duckdb.connect(":memory:")
    with pytest.raises(CounterfactualError, match="requires a stored rejection"):
        record_outcome(con, CounterfactualOutcome("s1", 1, date(2026, 11, 23), 0, None, HASH))
    record_rejection(con, RejectedSignal("s1", "RISK_GATE", NOW, HASH))
    with pytest.raises(CounterfactualError, match="conflicts"):
        record_rejection(con, RejectedSignal("s1", "LIQUIDITY_GATE", NOW, HASH))
