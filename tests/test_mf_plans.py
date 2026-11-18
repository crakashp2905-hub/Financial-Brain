from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.execution.mf_plans import (
    Ack,
    Evidence,
    NavPolicy,
    Payment,
    PlanError,
    Realism,
    Request,
    SchemeRules,
    check_purchase,
    check_redemption,
    idcw,
    mark,
    nav_trigger,
    redeemable_units,
    sip,
    stp,
    switch,
    swp,
)

T = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)
RULES = SchemeRules(D(5000), D(1000), D(500))
ELSS = SchemeRules(D(500), D(500), D(500), lock_in_days=1095)


def test_request_states():
    r = Request("R", T)
    with pytest.raises(PlanError):
        r.accept(T)
    r.acknowledge(); r.accept(T + timedelta(minutes=1))
    assert r.state is Ack.ACCEPTED
    with pytest.raises(PlanError):
        r.reject("late")


def test_payment_availability_not_inferred():
    p = Payment(T, credited_at=T + timedelta(hours=1))
    assert p.funds_available() is None and p.evidence is Evidence.ASSUMED


def test_minimums_and_closed_scheme():
    check_purchase(RULES, D(5000), first=True)
    with pytest.raises(PlanError):
        check_purchase(RULES, D(4999), first=True)
    check_purchase(RULES, D(1000), first=False)
    with pytest.raises(PlanError):
        check_purchase(SchemeRules(D(1), D(1), D(1), closed_for_purchase=True), D(10), True)


def test_elss_lock_in_and_gating():
    lots = [(date(2023, 1, 1), D(10)), (date(2026, 1, 1), D(5))]
    assert redeemable_units(ELSS, lots, date(2026, 11, 17)) == D(10)
    with pytest.raises(PlanError):
        check_redemption(ELSS, D(12), D(1000), lots, date(2026, 11, 17))
    gated = SchemeRules(D(1), D(1), D(1), gated=True)
    assert redeemable_units(gated, lots, date(2026, 11, 17)) == 0


def test_sip_missed_installment_stays_missed():
    legs = sip(date(2026, 1, 1), 3, 5, D(1000), {date(2026, 1, 5): True, date(2026, 3, 5): True})
    assert [x.status for x in legs] == ["EXECUTED", "MISSED", "EXECUTED"]


def test_switch_and_stp_wait_for_settlement():
    out, inn = switch(date(2026, 11, 17), D(1000), 1)
    assert inn.due > out.due
    with pytest.raises(PlanError):
        switch(date(2026, 11, 17), D(1000), 0)
    legs = stp(date(2026, 1, 1), 2, 10, D(500), 1)
    assert [x.kind for x in legs] == ["STP_OUT", "STP_IN", "STP_OUT", "STP_IN"]
    assert len(swp(date(2026, 1, 1), 12, 1, D(100))) == 12


def test_idcw_payout_and_reinvest_preserve_value():
    p = idcw(D(100), D(20), D(2), reinvest=False)
    assert p.cash == D(200) and p.total_value_after == p.total_value_before
    r = idcw(D(100), D(20), D(2), reinvest=True)
    assert r.new_units == D("11.111") and r.total_value_after <= r.total_value_before
    with pytest.raises(PlanError):
        idcw(D(1), D(10), D(10), reinvest=False)


def test_nav_trigger_is_request_and_needs_publication():
    pol = NavPolicy(D("0.2"), D("-0.1"))
    assert nav_trigger(pol, D(10), D(12.5), date(2026, 11, 17), T, T - timedelta(hours=1)) is None
    s = nav_trigger(pol, D(10), D(12.5), date(2026, 11, 17), T, T)
    assert s and s.action == "REDUCE" and s.state == "NAV_PENDING"
    assert nav_trigger(pol, D(10), D(8.9), date(2026, 11, 17), T, T).action == "REDEEM"
    assert nav_trigger(pol, D(10), D(10.5), date(2026, 11, 17), T, T) is None


def test_mark_uses_only_published_nav_and_flags_stale():
    navs = [(date(2026, 11, 16), T - timedelta(hours=14), D(10)), (date(2026, 11, 17), T + timedelta(hours=10), D(11))]
    m = mark(D(5), navs, T, date(2026, 11, 17))
    assert m.value == D(50) and m.stale
    assert mark(D(5), navs[1:], T, date(2026, 11, 17)) is None


def test_realism_score():
    assert Realism(True, Evidence.ASSUMED, True, False).score() == 0.5
    assert Realism(True, Evidence.OBSERVED, True, True).score() == 1.0
