from __future__ import annotations

import pytest

from financial_brain.paper.loss_economics import LossEconomicsError, gross_cost_report


def test_separates_gross_negative_and_cost_erased_with_inr_and_weighted_rates():
    result = gross_cost_report([
        {"trade_id": "a", "gross_pnl": -100.0, "costs": 10.0, "net_pnl": -110.0, "notional": 10000.0},
        {"trade_id": "b", "gross_pnl": 50.0, "costs": 60.0, "net_pnl": -10.0, "notional": 5000.0},
        {"trade_id": "c", "gross_pnl": 100.0, "costs": 10.0, "net_pnl": 90.0, "notional": 20000.0},
    ])
    gross_loss = result["groups"]["GROSS_NEGATIVE"]
    erased = result["groups"]["COST_ERASED"]
    assert gross_loss["trade_count"] == 1 and gross_loss["gross_pnl_inr"] == -100.0
    assert gross_loss["net_pnl_inr"] == -110.0 and gross_loss["net_pnl_bp"] == pytest.approx(-110.0)
    assert erased["trade_count"] == 1 and erased["gross_pnl_inr"] == 50.0
    assert erased["costs_inr"] == 60.0 and erased["net_pnl_inr"] == -10.0
    assert erased["notional_share"] == pytest.approx(5000 / 35000)


def test_zero_gross_and_zero_net_are_separate_from_cost_erased():
    report = gross_cost_report([
        {"trade_id": "flat", "gross_pnl": 0.0, "costs": 0.0, "net_pnl": 0.0, "notional": 1.0},
        {"trade_id": "erase", "gross_pnl": 5.0, "costs": 5.0, "net_pnl": 0.0, "notional": 2.0},
    ])
    assert report["groups"]["GROSS_FLAT"]["trade_count"] == 1
    assert report["groups"]["COST_ERASED"]["trade_count"] == 1


def test_empty_population_and_non_reconciled_or_invalid_rows():
    assert gross_cost_report([])["trade_count"] == 0
    bad = {"trade_id": "x", "gross_pnl": 10.0, "costs": 2.0, "net_pnl": 9.0, "notional": 100.0}
    with pytest.raises(LossEconomicsError, match="does not reconcile"):
        gross_cost_report([bad])
    with pytest.raises(LossEconomicsError, match="positive"):
        gross_cost_report([{**bad, "notional": 0.0, "net_pnl": 8.0}])


def test_daily_report_shows_gross_negative_and_cost_erased_counts(tmp_path):
    from datetime import datetime
    from financial_brain.paper import daily_results as DR
    from financial_brain.paper import intraday as I
    from financial_brain.paper import intraday_runner as R

    con = R.ledger(tmp_path / "trades.duckdb")
    engine = I.DayEngine("2026-10-07")
    engine.add_symbol("ABC", 100.0)
    engine.set_opening("ABC", open_px=100.0, or_hi=101.0, or_lo=99.0)
    for trade in engine.on_price("ABC", datetime(2026, 10, 7, 9, 35), 101.5):
        R.save(con, trade, "replay")
    for trade in engine.end_of_day(datetime(2026, 10, 7, 15, 20), {"ABC": 103.0}):
        R.save(con, trade, "replay")
    report = DR.build(con, "2026-10-07")
    assert "Gross-negative versus gross-positive cost-erased trades" in report
    assert "GROSS_NEGATIVE" in report and "COST_ERASED" in report
