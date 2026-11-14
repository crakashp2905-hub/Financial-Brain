from __future__ import annotations

import pytest

from financial_brain.paper.loss_attribution import (
    COMPONENTS,
    LossAttributionError,
    daily_loss_bridge,
)


def _row(day="2026-11-12", trade_id="t-1", **overrides):
    row = {"day": day, "trade_id": trade_id}
    row.update({name: 0.0 for name in COMPONENTS})
    row["forecast_gross_inr"] = 100.0
    row["selection_effect_inr"] = -60.0
    row["spread_effect_inr"] = -10.0
    row["impact_effect_inr"] = -5.0
    row["charges_effect_inr"] = -15.0
    row["timing_effect_inr"] = -5.0
    row["risk_sizing_effect_inr"] = -5.0
    row["realized_net_inr"] = 0.0
    row.update(overrides)
    return row


def test_losing_day_reconciles_all_effects_to_portfolio_loss():
    row = _row(realized_net_inr=-20.0, selection_effect_inr=-80.0)
    report = daily_loss_bridge([row])
    day = report["days"][0]
    assert report["status"] == day["status"] == "RECONCILED"
    assert day["portfolio_loss_inr"] == 20.0
    assert day["unexplained_residual_inr"] == 0.0


def test_multiple_trades_reconcile_by_day_and_keep_days_separate():
    a = _row("2026-11-12", "a", selection_effect_inr=-80.0, realized_net_inr=-20.0)
    b = _row("2026-11-12", "b", forecast_gross_inr=20.0, selection_effect_inr=-30.0,
             realized_net_inr=-50.0)
    c = _row("2026-11-13", "c", forecast_gross_inr=10.0, selection_effect_inr=0.0,
             spread_effect_inr=0.0, impact_effect_inr=0.0, charges_effect_inr=-10.0,
             timing_effect_inr=0.0, risk_sizing_effect_inr=0.0, realized_net_inr=0.0)
    report = daily_loss_bridge([a, b, c])
    assert [d["status"] for d in report["days"]] == ["RECONCILED", "RECONCILED"]
    assert report["days"][0]["realized_net_inr"] == -70.0
    assert report["days"][0]["portfolio_loss_inr"] == 70.0


def test_missing_component_is_not_filled_with_zero_and_residual_is_withheld():
    report = daily_loss_bridge([_row(spread_effect_inr=None)])
    day = report["days"][0]
    assert report["status"] == "INCOMPLETE"
    assert day["components_inr"]["spread_effect_inr"] is None
    assert day["unexplained_residual_inr"] is None
    assert day["missing_trade_ids_by_component"] == {"spread_effect_inr": ["t-1"]}


def test_nonzero_residual_cannot_be_labeled_reconciled():
    report = daily_loss_bridge([_row(realized_net_inr=-1.0)])
    assert report["days"][0]["status"] == "INCOMPLETE"
    assert report["days"][0]["unexplained_residual_inr"] == -1.0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "1"])
def test_nonfinite_or_non_numeric_effects_are_rejected(bad):
    with pytest.raises(LossAttributionError):
        daily_loss_bridge([_row(selection_effect_inr=bad)])


def test_duplicate_trade_id_and_invalid_tolerance_are_rejected():
    with pytest.raises(LossAttributionError, match="duplicate"):
        daily_loss_bridge([_row(), _row()])
    with pytest.raises(LossAttributionError, match="tolerance"):
        daily_loss_bridge([_row()], tolerance_inr=-0.1)
