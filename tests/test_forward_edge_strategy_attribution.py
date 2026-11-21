from datetime import date

import pytest

from financial_brain.forward_edge.strategy_attribution import (
    StrategyAttributionError,
    StrategyPnlComponents,
    attribute_strategy_pnl,
    require_ledger_reconciled,
)


def _components(*, mismatch=False):
    day = date(2026, 11, 16)
    return (StrategyPnlComponents("momentum", day, 100, -10, 2, 5, 3, 4, 1, 2,
                                  76 if mismatch else 77),
            StrategyPnlComponents("swing", day, -5, 4, 0.5, 0.5, 0.5, 0, 0, 0, -1.5))


def _report(mismatch=False):
    return attribute_strategy_pnl(session=date(2026, 11, 16), components=_components(mismatch=mismatch),
                                  strategy_weights={"momentum": 0.6, "swing": 0.4},
                                  covariance_matrix=((0.04, 0.06), (0.06, 0.09)))


def test_strategy_pnl_components_reconcile_and_risk_contributions_sum_to_portfolio_vol() -> None:
    report = _report()
    assert report.ledger_reconciled
    require_ledger_reconciled(report)
    assert report.portfolio_net_pnl == pytest.approx(75.5)
    assert report.portfolio_volatility == pytest.approx(0.24)
    assert sum(row["risk_contribution"] for row in report.strategy_rows) == pytest.approx(0.24)
    momentum = next(row for row in report.strategy_rows if row["strategy_id"] == "momentum")
    assert momentum["fees"] == 3
    assert momentum["slippage"] == 5
    assert momentum["ledger_difference"] == 0


def test_component_mismatch_is_visible_and_fails_closed() -> None:
    report = _report(mismatch=True)
    assert not report.ledger_reconciled
    with pytest.raises(StrategyAttributionError, match="do not reconcile"):
        require_ledger_reconciled(report)


def test_missing_strategy_covariance_or_negative_cost_is_rejected() -> None:
    with pytest.raises(StrategyAttributionError, match="weights must include"):
        attribute_strategy_pnl(session=date(2026, 11, 16), components=_components(),
                               strategy_weights={"momentum": 1}, covariance_matrix=((0.04, 0), (0, 0.09)))
    with pytest.raises(StrategyAttributionError, match="cost components"):
        StrategyPnlComponents("x", date(2026, 11, 16), 0, 0, 0, -1, 0, 0, 0, 0, 1)
