from __future__ import annotations

from datetime import date, time
from decimal import Decimal as D

import pytest

from financial_brain.research.mf_portfolio import (
    AllocationRecommendation,
    CashOption,
    CutoffBook,
    CutoffRule,
    MfResearchError,
    allocate,
    cash_management,
    concentration,
    debt_metrics,
    etf_display,
    lookthrough,
    mf_display,
    mf_period_return,
    overlap,
    reconcile_mf,
    tax_lot_action,
)


def book():
    b = CutoffBook()
    b.add_class("EQUITY", CutoffRule(date(2020, 1, 1), time(15, 0), "AMFI"))
    b.add_class("LIQUID", CutoffRule(date(2020, 1, 1), time(13, 30), "AMFI"))
    b.add_scheme("X1", CutoffRule(date(2026, 1, 1), time(14, 0), "SID X1"))
    b.suspended["X2"] = [(date(2026, 11, 10), date(2026, 11, 20))]
    return b


def test_cutoff_by_class_override_and_unknown():
    b = book()
    assert b.rule("A", "EQUITY", date(2026, 11, 17)).cutoff == time(15, 0)
    assert b.rule("L", "LIQUID", date(2026, 11, 17)).cutoff == time(13, 30)
    assert b.rule("X1", "EQUITY", date(2026, 11, 17)).cutoff == time(14, 0)
    assert b.rule("X1", "EQUITY", date(2025, 11, 17)).cutoff == time(15, 0)
    with pytest.raises(MfResearchError):
        b.rule("O", "OVERNIGHT", date(2026, 11, 17))
    with pytest.raises(MfResearchError):
        b.rule("X2", "EQUITY", date(2026, 11, 17))


def test_reconcile_mf():
    kw = dict(contributed=D(1000), cash_available=D(100), pending_applications=D(200),
              allotted_cost=D(600), redemption_pending=D(50), paid_out=D(50), realized_pnl=D(0))
    assert reconcile_mf(**kw) == []
    assert reconcile_mf(**{**kw, "paid_out": D(60)})


def test_debt_metrics_label_absent():
    m = debt_metrics({"ytm": D("7.1")})
    assert m["ytm"] == D("7.1") and m["modified_duration"] == "ABSENT"


def test_lookthrough_fof_and_coverage():
    exp, cov = lookthrough({"FOF": D(100), "DIRECT_STOCK": D(100)},
                           {"FOF": {"FUND_A": D(1), "FUND_B": D(1)}, "FUND_A": {"RELIANCE": D(1)}})
    assert exp == {"RELIANCE": D(50), "FUND_B": D(50), "DIRECT_STOCK": D(100)}
    assert cov == D("0.5")
    with pytest.raises(MfResearchError):
        lookthrough({"A": D(1)}, {"A": {"B": D(1)}, "B": {"A": D(1)}})


def test_concentration_and_overlap():
    assert concentration({"a": D(1), "b": D(1)}) == D("0.5")
    assert overlap({"a": D(1), "b": D(1)}, {"a": D(3), "c": D(1)}) == D("0.5")


def test_cash_management_counts_payout_lag():
    rows = cash_management([CashOption("liquid", 0.07, 1, 0.0), CashOption("savings", 0.03, 0, 0.0),
                            CashOption("ultra", 0.075, 3, 0.002)], 30)
    assert rows[0][0] == "liquid"


def test_allocate_can_choose_cash():
    w = allocate({"A": 0.08, "B": -0.01}, {"A": 0.2, "B": 0.1}, budget_risk=0.05, max_weight=0.6)
    assert "B" not in w and w["A"] == pytest.approx(0.25) and w["CASH"] == pytest.approx(0.75)
    assert allocate({"A": -0.1}, {"A": 0.1}, budget_risk=1, max_weight=1) == {"CASH": 1.0}


def test_display_records():
    card = mf_display("X", "DIRECT", "GROWTH", D("12.3"), date(2026, 11, 16), True, D(1), D(10), D(0),
                      D(0), "15:00 IST")
    assert card["allotment"] == "PENDING" and "BUY_AT_PRICE" not in card["actions"]
    e = etf_display("E", "Nifty 50 TRI", D(100), D("100.1"), None, None, D(5), "LIQUID")
    assert e["premium_vs_inav"] == "UNAVAILABLE"
    r = AllocationRecommendation("X", "SUBSCRIBE", D(1000), "15:00 IST")
    assert r.nav_status == "NAV_PENDING"


def test_flow_aware_return():
    assert mf_period_return(D(1000), D(2100), D(1000), D(0), D(0)) == D("0.05")
    assert mf_period_return(D(1000), D(900), D(0), D(100), D(50)) == D("0.05")


def test_tax_lot_action():
    lots = [(date(2026, 6, 1), D(10), D(100))]
    assert tax_lot_action(lots, date(2026, 11, 17), D(12), 365, 365) == "HOLD"
    assert tax_lot_action([(date(2024, 1, 1), D(10), D(100))], date(2026, 11, 17), D(12), 365, 365) == "REVIEW"
