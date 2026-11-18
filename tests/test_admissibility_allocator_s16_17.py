from __future__ import annotations

import threading
from decimal import Decimal as D

import pytest

from financial_brain.risk.capital_allocator import AllocationError, CapitalAllocator
from financial_brain.signals.admissibility import Costs, Decision, Reason, Scenario, decide, net_ev


def scen(pt="0.40", ps="0.40", reward="6", risk="3", tm="0", gap="0", cal=True, n=500):
    return Scenario(D(pt), D(ps), D(reward), D(risk), D(tm), D(gap), cal, n)


COSTS = Costs(D("0.5"), "costs-v1")


def go(s=None, c=COSTS, **kw):
    args = dict(units=10, quote_ok=True, spread_ok=True, expired=False, halted=False,
                budget_ok=True, hurdle_per_unit=D("0.1"), min_samples=100)
    args.update(kw)
    return decide(scen() if s is None else s, c, **args)


def test_net_ev_formula():
    # 0.4*6 - 0.4*3 + 0.2*0 - 0.5 = 0.7
    assert net_ev(scen(), COSTS) == D("0.7")


def test_positive_net_edge_trades_with_receipt():
    r = go()
    assert r.decision is Decision.TRADE and r.net_ev_total == D("7.0")
    assert r.risk_at_stop_inr == D(30) and r.cost_model == "costs-v1"


def test_two_to_one_without_hit_rate_is_not_an_edge():
    r = go(scen(pt="0.30", ps="0.70"))
    assert r.decision is Decision.CASH


def test_gross_edge_eaten_by_costs_is_skipped():
    r = go(scen(pt="0.40", ps="0.40"), Costs(D("1.5"), "v"))
    assert r.decision is Decision.CASH and r.reason is Reason.COST


@pytest.mark.parametrize("kw,reason", [
    ({"quote_ok": False}, Reason.STALE_FEED), ({"expired": True}, Reason.EXPIRED),
    ({"halted": True}, Reason.HALTED),
])
def test_feed_expiry_halt_reject(kw, reason):
    r = go(**kw)
    assert r.decision is Decision.REJECT and r.reason is reason


def test_missing_costs_never_trade():
    assert go(c=None).reason is Reason.MISSING_INPUT


def test_nan_input_rejected():
    assert go(scen(reward="NaN")).decision is Decision.REJECT


def test_uncalibrated_or_small_sample_is_watch():
    assert go(scen(cal=False)).reason is Reason.UNCALIBRATED
    assert go(scen(n=10)).reason is Reason.CONFIDENCE_LOW


def test_bad_probabilities_rejected():
    assert go(scen(pt="0.8", ps="0.5")).decision is Decision.REJECT


# ---- allocator


def alloc(**kw):
    a = dict(capital=D(1000), desk_limits={"SWING": D(600), "ETF": D(600)},
             underlying_limits={"NIFTY": D(500)}, daily_loss_limit=D(100))
    a.update(kw)
    return CapitalAllocator(**a)


def test_desk_and_underlying_limits():
    a = alloc()
    assert a.reserve("k1", "SWING", "NIFTY", D(400))
    assert not a.reserve("k2", "ETF", "NIFTY", D(200))   # NIFTY overlap across desks
    assert a.reserve("k3", "ETF", "GOLD", D(500))
    assert not a.reserve("k4", "SWING", "X", D(300))      # total capital


def test_reservation_idempotent_and_release():
    a = alloc()
    assert a.reserve("k1", "SWING", "X", D(100)) and a.reserve("k1", "SWING", "X", D(100))
    assert a.available == D(900)
    a.release("k1")
    assert a.available == D(1000)


def test_kill_switch_needs_human_reset():
    a = alloc()
    a.record_pnl(D(-100))
    assert not a.reserve("k", "SWING", "X", D(1))
    with pytest.raises(AllocationError):
        a.human_reset(confirmed_by="")
    a.human_reset(confirmed_by="owner")
    assert a.reserve("k", "SWING", "X", D(1))


def test_quarantine_blocks_desk_only():
    a = alloc(); a.quarantine("ETF")
    assert not a.reserve("k", "ETF", "X", D(1)) and a.reserve("j", "SWING", "X", D(1))


def test_concurrent_burst_never_overspends():
    a = alloc(desk_limits={"SWING": D(1000)}, underlying_limits={})
    ts = [threading.Thread(target=a.reserve, args=(f"k{i}", "SWING", "X", D(30))) for i in range(100)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert a.available >= 0 and len(a.reservations) == 33
