from financial_brain.paper import engine
from financial_brain.paper.execution_audit import audit


def test_execution_audit_preserves_simulated_and_unfilled_facts():
    run = engine.Run("audit-run", {})
    run.trades.extend([
        engine.Trade(None, "A", "buy", 80, 100.0, 8000.0, 10.0, engine.SIMULATED_FILL,
                     intended_shares=100),
        engine.Trade(None, "B", "sell", 50, 200.0, 10000.0, 8.0, engine.CLOSE_FILL,
                     intended_shares=50),
    ])
    result = audit(run)
    assert result["fill_kinds"] == {engine.SIMULATED_FILL: 1, engine.CLOSE_FILL: 1}
    assert result["unfilled_shares"] == 20
    assert result["fill_ratio"] == 130 / 150
    assert result["paper_only"] is True
