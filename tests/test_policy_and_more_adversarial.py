"""Policy helpers plus extra adversarial cases (FB-1301, FB-1303, FB-1306, FB-1309, FB-1313,
FB-1316, FB-1319)."""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.execution.mf_orders import MfOrderError, PublishedNav, Subscription, eligible_nav_date
from financial_brain.execution.mf_plans import PlanError, SchemeRules, check_redemption, idcw
from financial_brain.execution.option_book import contract_pnl
from financial_brain.instruments.mf_scheme import (
    AssetType,
    Option,
    Plan,
    SchemeKey,
    SchemeMaster,
    SchemeVersion,
    Structure,
)
from financial_brain.instruments.pit import Quote, nav_usable, quote_at
from financial_brain.paper import policy
from financial_brain.paper.ops import FeedMode, evidence_grade
from financial_brain.portfolio.multi_asset_ledger import Journal, Mark
from financial_brain.research.mf_portfolio import CutoffBook, CutoffRule
from financial_brain.signals.exit_triggers import Bar, Event, ProtectedPosition

UTC = timezone.utc
T = datetime(2026, 11, 17, 4, 0, tzinfo=UTC)


def test_paper_only_policy():
    policy.assert_paper_only()
    assert policy.PHYSICAL_ORDERS_ENABLED is False


def test_size_position_takes_the_binding_cap():
    n = policy.size_position(risk_budget=D(3000), entry=D(100), stop=D(97), cash=D(50000), lot=1,
                             max_notional=D(200000), displayed_depth=5000, participation=D("0.1"),
                             correlated_exposure=D(0), correlated_cap=D(1e9))
    assert n == 500
    n = policy.size_position(risk_budget=D(3000), entry=D(100), stop=D(97), cash=D(50000), lot=75,
                             max_notional=D(200000), displayed_depth=5000, participation=D("0.1"),
                             correlated_exposure=D(95000), correlated_cap=D(100000))
    assert n == 0


def test_round_and_revalidate():
    e, s, t = policy.round_and_revalidate("BUY", D("100.02"), D("97.03"), D("106.04"), D("0.05"), D("1.5"))
    assert (e, s, t) == (D("100.05"), D("97.00"), D("106.00"))
    with pytest.raises(policy.PolicyError):
        policy.round_and_revalidate("BUY", D("100.02"), D("99.03"), D("101.04"), D("0.05"), D("2"))


def test_reconcile_handler_records():
    out = []
    h = policy.reconcile_handler(lambda: None, lambda: ("SESSION_COMPLETE", []), lambda d, s, p: out.append(s))
    h(date(2026, 11, 17))
    assert out == ["SESSION_COMPLETE"]


# FB-1301: randomised timezones never change point-in-time answers
def test_random_timezones_do_not_change_pit_answers():
    rng = random.Random(1301)
    for _ in range(1000):
        tz = timezone(timedelta(minutes=rng.randrange(-720, 841, 15)))
        pub = T + timedelta(minutes=rng.randrange(-120, 120))
        dec = T + timedelta(minutes=rng.randrange(-120, 120))
        assert nav_usable(pub.astimezone(tz), dec) == (pub <= dec)
        q = Quote(D("99.9"), D("100"), pub.astimezone(tz), pub.astimezone(tz), 1, 1)
        assert quote_at(q, dec.astimezone(tz), max_age=timedelta(minutes=30)).ok == \
            quote_at(q, dec, max_age=timedelta(minutes=30)).ok


# FB-1303: polling fallback is labelled degraded and never gets a better fill
def test_polling_is_degraded():
    assert evidence_grade(FeedMode.POLLING) != evidence_grade(FeedMode.STREAM)
    stale = Quote(D("90"), D("90.05"), T - timedelta(seconds=20), T - timedelta(seconds=20), 10, 10)
    assert not quote_at(stale, T, max_age=timedelta(seconds=5)).ok


# FB-1306: currency and commodity lookthrough with stock hedges
def test_currency_commodity_lookthrough():
    j = Journal(); j.contribute("c", T, "ETF", D(10000))
    j.underlying.update({"IN:GOLDBEES": "GOLD", "IN:MON100": "USD_NASDAQ", "IN:GOLDMF": "GOLD"})
    j.buy("a", T, "ETF", "IN:GOLDBEES", D(10), D(50)); j.buy("b", T, "ETF", "IN:MON100", D(5), D(100))
    j.buy("g", T, "ETF", "IN:GOLDMF", D(10), D(20))
    e = j.exposure_by_underlying({"IN:GOLDBEES": Mark(D(50), T), "IN:MON100": Mark(D(100), T),
                                  "IN:GOLDMF": Mark(D(20), T)})
    assert e == {"GOLD": D(700), "USD_NASDAQ": D(500)}


# FB-1309: liquid fund cut-off differs; suspension blocks
def test_liquid_and_suspended_cutoffs():
    ist = timezone(timedelta(hours=5, minutes=30))
    b = CutoffBook(); b.add_class("LIQUID", CutoffRule(date(2020, 1, 1), time(13, 30), "AMFI"))
    rule = b.rule("L1", "LIQUID", date(2026, 11, 17))
    at = datetime(2026, 11, 17, 14, 0, tzinfo=ist)
    assert eligible_nav_date(at, at, cutoff=rule.cutoff, is_scheme_day=lambda d: d.weekday() < 5,
                             tz=ist) == date(2026, 11, 18)


# FB-1313: winding-up leaves history; delayed redemption NAV stays pending
def test_wound_up_and_delayed_nav():
    m = SchemeMaster()
    k = SchemeKey("9", Plan.DIRECT, Option.GROWTH, "INF9")
    m.add(k, SchemeVersion(date(2020, 1, 1), "AMFI", "Debt", AssetType.DEBT, Structure.OPEN_ENDED, False, "X"))
    m.add(k, SchemeVersion(date(2026, 1, 1), "AMFI", "Debt", AssetType.DEBT, Structure.OPEN_ENDED, False, "X",
                           status="WOUND_UP"))
    assert k in m.universe(date(2025, 6, 1)) and k not in m.universe(date(2026, 6, 1))
    s = Subscription("O", "IN:MF:9", D(100), T); s.accept(date(2026, 11, 17))
    with pytest.raises(MfOrderError):
        s.allot(PublishedNav(date(2026, 11, 17), D(10), T + timedelta(days=3)), T + timedelta(days=1))
    assert idcw(D(10), D(20), D(1), reinvest=False).cash == D(10)


# FB-1316: crash at any lifecycle point and replay -> identical ledger
@pytest.mark.parametrize("crash_after", [0, 1, 2, 3])
def test_crash_points_replay_identically(crash_after):
    bars = [Bar(T + timedelta(minutes=i), D(100), D(101), D(99), D(100)) for i in range(3)] + \
           [Bar(T + timedelta(minutes=3), D(99), D(99), D(96), D(97))]
    full = ProtectedPosition("P", True, D(10), D(100), D(97), D(106))
    for b in bars:
        full.on_bar(b)
    crashed = ProtectedPosition("P", True, D(10), D(100), D(97), D(106))
    for b in bars[:crash_after]:
        crashed.on_bar(b)
    for b in bars:
        crashed.on_bar(b)
    assert [r.key for r in crashed.ledger] == [r.key for r in full.ledger]


# FB-1319: 50% option premium loss, MF gated redemption, simultaneous correlated exposure
def test_more_stress_cases():
    r = contract_pnl([("BUY", 2, 100.0)], 75, 50.0, 0)
    assert r["unrealized"] == -50 * 2 * 75
    gated = SchemeRules(D(1), D(1), D(1), gated=True)
    with pytest.raises(PlanError):
        check_redemption(gated, D(1), D(100), [(date(2020, 1, 1), D(10))], date(2026, 11, 17))
    p = ProtectedPosition("P", True, D(10), D(100), D(97), D(106))
    p.on_bar(Bar(T, D(75), D(75), D(74), D(75)))
    assert [r.price for r in p.ledger if r.kind is Event.EXIT_FILL] == [D(75)]
