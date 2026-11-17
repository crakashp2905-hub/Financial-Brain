from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.signals.exit_plans import (
    Basis,
    ExitRule,
    ExitSignal,
    Leg,
    Level,
    ManagedExit,
    PlanError,
    close_legs,
    closing_side,
    paired_report,
    same_basis,
)

T = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("side,close", [("BUY", "SELL_TO_CLOSE"), ("SELL_SHORT", "BUY_TO_COVER")])
def test_closing_side(side, close):
    assert closing_side(side) == close


def test_unknown_side_refused():
    with pytest.raises(PlanError):
        closing_side("SELL")


def test_spread_closes_every_leg_on_active_tokens():
    legs = [Leg("C1", "BUY", 75), Leg("C2", "SELL", 75)]
    assert close_legs(legs, {"C1", "C2"}) == [Leg("C1", "SELL", 75), Leg("C2", "BUY", 75)]
    with pytest.raises(PlanError):
        close_legs(legs, {"C1"})


def test_levels_on_different_basis_not_compared():
    same_basis(Level(D(1), Basis.ETF_QUOTE, "STOP"), Level(D(2), Basis.ETF_QUOTE, "TARGET"))
    with pytest.raises(PlanError):
        same_basis(Level(D(1), Basis.UNDERLYING, "STOP"), Level(D(2), Basis.OPTION_PREMIUM, "TARGET"))


RULE = ExitRule("ladder-v1", ((D(105), D("0.5")), (D(110), D("0.5"))), True, D(4))


def managed():
    return ManagedExit("P", D(100), 10, Level(D(97), Basis.EQUITY_QUOTE, "STOP"), RULE)


def test_ladder_breakeven_and_trailing():
    m = managed()
    assert m.on_price(T, D(105)) == [("TP1", 5, D(105))]
    assert m.current_stop == D(101)               # trailing 105-4 beats breakeven 100
    assert [w for _, _, w in m.stops] == ["ORIGINAL", "BREAKEVEN", "TRAIL"]
    assert m.on_price(T, D(108)) == []
    assert m.current_stop == D(104)
    assert m.on_price(T, D(103.5)) == [("STOP", 5, D("103.5"))]
    assert m.remaining == 0 and m.on_price(T, D(90)) == []


def test_original_plan_preserved_in_revisions():
    m = managed(); m.on_price(T, D(106))
    assert m.stops[0][1] == D(97)


def test_no_overselling_with_rounding():
    rule = ExitRule("v", ((D(101), D("0.34")), (D(102), D("0.34")), (D(103), D("0.34"))), False, None)
    with pytest.raises(PlanError):
        ManagedExit("P", D(100), 3, Level(D(99), Basis.EQUITY_QUOTE, "STOP"), rule)
    rule2 = ExitRule("v", ((D(101), D("0.5")), (D(102), D("0.5"))), False, None)
    m = ManagedExit("P", D(100), 3, Level(D(99), Basis.EQUITY_QUOTE, "STOP"), rule2)
    total = sum(q for _, q, _ in m.on_price(T, D(103)))
    assert total <= 3 and m.remaining == 3 - total


def test_exit_signal_and_paired_report():
    e1 = ExitSignal("X1", "P", "S", "TP1", D(105), D(105), 5, D(2), T)
    e2 = ExitSignal("X2", "P", "S", "STOP", D(104), None, 5, D(0), T + timedelta(minutes=5))
    r = paired_report("S", T, D(100), "breakout", D(97), D(110), [], [e1, e2])
    assert r["net_pnl"] == "23" and r["unresolved"] is True
    assert r["r_multiple"] == str(D(23) / D(15))
    assert e2.pnl(D(100)) is None
