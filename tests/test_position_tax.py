from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.portfolio.position_ledger import (
    ALERT_KINDS,
    Ev,
    LedgerError,
    PositionLedger,
    Record,
    ablate,
    attribution_chain,
    by_exit_reason,
    compare_sleeves,
    mtm,
)
from financial_brain.portfolio.tax_lots import (
    Flows,
    Lot,
    TaxBook,
    TaxError,
    TaxRule,
    desk_contribution,
    fy,
    sell_fifo,
)

T = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)


def rec(k, kind, pid="P", dt=0, **snap):
    return Record(k, pid, "AUTONOMOUS", "IN:X", kind, T + timedelta(minutes=dt),
                  tuple(sorted((a, str(b)) for a, b in snap.items())))


def test_lifecycle_order_idempotent_and_close_needs_fill():
    led = PositionLedger()
    assert led.append(rec("1", Ev.ENTRY_SIGNAL, sl=97, tp=106))
    assert not led.append(rec("1", Ev.ENTRY_SIGNAL))
    with pytest.raises(LedgerError):
        led.append(rec("x", Ev.ENTRY_SIGNAL))
    led.append(rec("2", Ev.ENTRY_INTENT, dt=1)); led.append(rec("3", Ev.ENTRY_FILL, dt=2))
    led.append(rec("4", Ev.STOP_TP_ARMED, dt=3))
    with pytest.raises(LedgerError):
        led.append(rec("5", Ev.POSITION_CLOSED, dt=4))
    with pytest.raises(LedgerError):
        led.append(rec("6", Ev.ENTRY_INTENT, dt=4))
    led.append(rec("7", Ev.EXIT_TRIGGER, dt=5)); led.append(rec("8", Ev.EXIT_FILL, dt=6))
    led.append(rec("9", Ev.POSITION_CLOSED, dt=7))
    with pytest.raises(LedgerError):
        led.append(rec("10", Ev.EXIT_FILL, dt=8))
    assert led.original_plan("P") == {"sl": "97", "tp": "106"}
    assert len(attribution_chain(led, "P")) == 7


def test_event_before_entry_refused():
    with pytest.raises(LedgerError):
        PositionLedger().append(rec("1", Ev.ENTRY_FILL))


def test_human_decision_does_not_edit_plan():
    led = PositionLedger()
    led.append(rec("1", Ev.ENTRY_SIGNAL, sl=97))
    led.append(rec("h", Ev.HUMAN_DECISION, decision="APPROVE", sl=95))
    assert led.original_plan("P")["sl"] == "97"


def test_alert_kinds_complete():
    assert {"STOP_HIT", "EXIT_UNPRICED", "MF_NAV_PENDING", "POSITION_CLOSED"} <= set(ALERT_KINDS)


def test_mtm_live_only_with_fresh_quote():
    r = mtm("P", 100, 10, 97, 106, 103, T, T + timedelta(seconds=2), 5, 4)
    assert r.live and r.unrealized == 26
    s = mtm("P", 100, 10, 97, 106, 103, T, T + timedelta(minutes=2), 5, 4)
    assert not s.live and s.unrealized is None


def test_exit_reason_and_ablation():
    r = by_exit_reason([("TP", 60), ("SL", -30), ("SL", -30)])
    assert r["SL"]["n"] == 2 and r["TP"]["net"] == 60
    assert ablate([10, -5], [12, -6]) == 0.5
    with pytest.raises(LedgerError):
        ablate([1], [1, 2])


def test_sleeve_comparison_separates_selection_and_execution():
    r = compare_sleeves(["a", "b", "c"], {"a": 10, "b": -5, "c": 3}, {"a": 8, "c": 3})
    assert r["paired"] == 2 and r["execution_effect"] == -2 and r["selection_effect"] == 5


def book():
    b = TaxBook()
    b.rules.append(TaxRule(date(2024, 7, 23), "EQUITY_ORIENTED", 365, D("0.20"), D("0.125"), D(125000), "FA 2024"))
    b.classes["IN:MF:EQ"] = [(date(2020, 1, 1), "EQUITY_ORIENTED")]
    return b


def test_fifo_lots_terms_and_fy():
    lots = [Lot("IN:MF:EQ", date(2025, 1, 1), D(10), D(1000)), Lot("IN:MF:EQ", date(2026, 6, 1), D(10), D(1500))]
    out = sell_fifo(lots, "IN:MF:EQ", D(15), D(200), date(2026, 11, 17), book())
    assert [r.term for r in out] == ["LONG", "SHORT"]
    assert out[0].gain == D(1000) and out[1].gain == D(250)
    assert out[1].est_tax == D(50) and out[0].report_year == "FY2026-27"
    assert lots[0].units == D(5) and lots[0].cost == D(750)


def test_tax_missing_class_or_rule_raises():
    with pytest.raises(TaxError):
        sell_fifo([Lot("IN:Z", date(2025, 1, 1), D(1), D(1))], "IN:Z", D(1), D(2), date(2026, 1, 1), book())
    b = book(); b.classes["IN:G"] = [(date(2020, 1, 1), "GOLD_INTL")]
    with pytest.raises(TaxError):
        sell_fifo([Lot("IN:G", date(2025, 1, 1), D(1), D(1))], "IN:G", D(1), D(2), date(2026, 1, 1), b)


def test_flows_and_contribution():
    f = Flows(); f.add(date(2026, 1, 1), D(1000)); f.add(date(2026, 2, 1), D(-200))
    assert f.net() == D(800)
    assert desk_contribution({"SWING": 100, "ETF": -50}, 10000) == {"SWING": 0.01, "ETF": -0.005}
    assert fy(date(2026, 3, 31)) == "FY2025-26"
