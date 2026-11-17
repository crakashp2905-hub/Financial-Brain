"""Section 13: adversarial and property-style tests over the five-desk paper modules.

Randomised sequences use a fixed seed so failures reproduce.
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.execution.exchange_paper import Account, ExchangePaperOms, PaperOrder
from financial_brain.execution.mf_orders import (
    Holding,
    Lot,
    LoadRule,
    MfOrderError,
    PublishedNav,
    Subscription,
    eligible_nav_date,
)
from financial_brain.instruments.pit import Inav, Quote, inav_premium
from financial_brain.portfolio.multi_asset_ledger import Journal, LedgerError, Mark
from financial_brain.signals.admissibility import Costs, Decision, Scenario, decide
from financial_brain.signals.exit_triggers import Bar, Event, ProtectedPosition

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
T = datetime(2026, 11, 17, 4, 0, tzinfo=UTC)


# FB-1301 / FB-1317: 1000+ randomised ledger events keep debits = credits and never overspend.
def test_randomised_ledger_sequences_preserve_invariants():
    rng = random.Random(1301)
    j = Journal()
    j.contribute("c", T, "SWING", D(100000))
    for _ in range(1200):
        inst = f"IN:{rng.randrange(5)}"
        key = f"e{rng.randrange(900)}"   # repeated keys simulate duplicate network events
        price = D(rng.randrange(50, 150))
        try:
            if rng.random() < 0.5:
                j.buy(key, T, "SWING", inst, D(rng.randrange(1, 20)), price)
            elif rng.random() < 0.8:
                j.sell(key, T, "SWING", inst, D(rng.randrange(1, 20)), price)
            else:
                j.settle(key, T, "SWING", D(rng.randrange(1, 500)))
        except LedgerError:
            pass
        j.check()
        assert j.cash("AVAILABLE", "SWING") >= 0
        assert all(u >= 0 for u in j.units.values())
    assert len({e.key for e in j.entries}) == len(j.entries)


# FB-1302: no forced trade when expected net does not cover costs.
@pytest.mark.parametrize("cost", ["0.8", "1.0", "5"])
def test_no_forced_trade_when_net_does_not_cover_costs(cost):
    s = Scenario(D("0.4"), D("0.4"), D(6), D(3), D(0), D(0), True, 500)
    r = decide(s, Costs(D(cost), "v"), units=1, quote_ok=True, spread_ok=True, expired=False,
               halted=False, budget_ok=True, hurdle_per_unit=D("0.5"), min_samples=100)
    assert r.decision is not Decision.TRADE


# FB-1303: a delayed quote must not fill at a better price.
def test_delayed_quote_does_not_fill():
    m = ExchangePaperOms(max_quote_age=timedelta(seconds=5))
    m.submit(PaperOrder("O", "S", Account.AUTONOMOUS, "INTRADAY", "IN:X", "BUY", 10, D(100),
                        T + timedelta(hours=1)))
    old = Quote(D("90.00"), D("90.05"), T, T, 100, 100)
    assert m.on_quote("O", old, T + timedelta(seconds=30)) is None


# FB-1305: stale iNAV never yields a premium; wide spread blocks fills through the limit.
def test_stale_inav_and_wide_spread():
    q = Quote(D("95"), D("105"), T, T, 100, 100)
    p = inav_premium(q, Inav(D(100), T - timedelta(minutes=10)), max_skew=timedelta(seconds=5),
                     max_inav_age=timedelta(minutes=1), decision_at=T)
    assert p.fraction is None
    m = ExchangePaperOms(max_quote_age=timedelta(seconds=5))
    m.submit(PaperOrder("O", "S", Account.AUTONOMOUS, "ETF", "IN:E", "BUY", 1, D(100), T + timedelta(hours=1)))
    assert m.on_quote("O", q, T) is None


# FB-1306 / FB-1315: an ETF and an index fund on the same underlying are not diversifiers.
def test_lookthrough_sums_same_underlying():
    j = Journal()
    j.contribute("c", T, "ETF", D(10000)); j.contribute("d", T, "MUTUAL_FUND", D(10000))
    j.underlying.update({"IN:ETF": "NIFTY", "IN:MF:IDX": "NIFTY"})
    j.buy("b", T, "ETF", "IN:ETF", D(10), D(100))
    j.mf_request("m", T, "MUTUAL_FUND", D(1000))
    j.mf_allot("a", T, "MUTUAL_FUND", "IN:MF:IDX", D(1000), D(50))
    exp = j.exposure_by_underlying({"IN:ETF": Mark(D(100), T), "IN:MF:IDX": Mark(D(20), T)})
    assert list(exp) == ["NIFTY"] and exp["NIFTY"] == D(2000)


# FB-1309: cut-off, late funds, weekends and holidays.
HOLIDAY = date(2026, 11, 18)


def scheme_day(d: date) -> bool:
    return d.weekday() < 5 and d != HOLIDAY


@pytest.mark.parametrize("acc,funds,expected", [
    (datetime(2026, 11, 17, 14, 59, tzinfo=IST), datetime(2026, 11, 17, 14, 59, tzinfo=IST), date(2026, 11, 17)),
    (datetime(2026, 11, 17, 15, 0, tzinfo=IST), datetime(2026, 11, 17, 15, 0, tzinfo=IST), date(2026, 11, 19)),
    (datetime(2026, 11, 17, 10, 0, tzinfo=IST), datetime(2026, 11, 19, 16, 0, tzinfo=IST), date(2026, 11, 20)),
    (datetime(2026, 11, 21, 10, 0, tzinfo=IST), datetime(2026, 11, 21, 10, 0, tzinfo=IST), date(2026, 11, 23)),
])
def test_cutoff_matrix(acc, funds, expected):
    assert eligible_nav_date(acc, funds, cutoff=time(15, 0), is_scheme_day=scheme_day, tz=IST) == expected


# FB-1310: NAV arriving after the decision cannot allot.
def test_nav_after_decision_cannot_allot():
    s = Subscription("O", "IN:MF:1", D(100), T)
    s.accept(date(2026, 11, 17))
    with pytest.raises(MfOrderError):
        s.allot(PublishedNav(date(2026, 11, 17), D(10), T + timedelta(hours=10)), T + timedelta(hours=5))


# FB-1311: insufficient available funding is rejected; pending cash is not reusable.
def test_insufficient_funding_rejected():
    j = Journal(); j.contribute("c", T, "MUTUAL_FUND", D(100))
    with pytest.raises(LedgerError):
        j.mf_request("m", T, "MUTUAL_FUND", D(101))
    j.mf_request("m", T, "MUTUAL_FUND", D(100))
    with pytest.raises(LedgerError):
        j.mf_request("n", T, "MUTUAL_FUND", D(1))


# FB-1312: partial redemption across lots with lot-specific loads.
def test_partial_redemption_lot_loads():
    h = Holding([Lot(date(2025, 1, 1), D("10.500"), D(105)), Lot(date(2026, 11, 10), D("5.250"), D(60))])
    nav = PublishedNav(date(2026, 11, 17), D(12), T)
    r = h.redeem(D("12.000"), nav, T + timedelta(hours=1), LoadRule(365, D("0.01")))
    assert r.load == D("1.500") * 12 * D("0.01")
    assert h.units == D("3.750")


# FB-1316: replaying the same bars after a crash is idempotent.
def test_exit_replay_after_restart_is_idempotent():
    p = ProtectedPosition("P", True, D(10), D(100), D(97), D(106))
    bars = [Bar(T + timedelta(minutes=i), D(100), D(101), D(99), D(100)) for i in range(5)]
    bars.append(Bar(T + timedelta(minutes=5), D(99), D(99), D(95), D(96)))
    for b in bars + bars:
        p.on_bar(b)
    assert sum(1 for r in p.ledger if r.kind is Event.EXIT_FILL) == 1


# FB-1319: 25% gap through the stop books the full gap loss, not the planned stop.
def test_twenty_five_percent_gap_stress():
    p = ProtectedPosition("P", True, D(10), D(100), D(97), D(106))
    p.on_bar(Bar(T, D(75), D(76), D(74), D(75)))
    assert p.realized() == D(-250)
