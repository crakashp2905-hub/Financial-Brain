"""4A table-driven, failure and property tests (FB-0490, FB-0492, FB-0493) plus FB-1307, FB-1315,
FB-2008 and FB-2439 adversarial cases over the paper modules."""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.execution.exchange_paper import Account, ExchangePaperOms, OrderStatus, PaperOrder
from financial_brain.execution.mf_orders import MfOrderError, PublishedNav, Subscription
from financial_brain.instruments.corporate_actions import Holding, Split, apply_split
from financial_brain.instruments.pit import Inav, Quote, inav_premium
from financial_brain.instruments.units import inr
from financial_brain.portfolio.multi_asset_ledger import Journal, LedgerError, Mark
from financial_brain.risk.capital_allocator import CapitalAllocator
from financial_brain.signals.contracts import (
    Desk,
    EvidenceTier,
    ExitPolicy,
    ExitTrigger,
    Intent,
    Side,
    Signal,
    SignalError,
)
from financial_brain.signals.exit_triggers import Bar, Event, ProtectedPosition
from financial_brain.signals.index_options import Contract, IndexSignalState, choose

T = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)
POL = ExitPolicy((ExitTrigger.HARD_STOP, ExitTrigger.TAKE_PROFIT, ExitTrigger.SESSION_FLATTEN), "v1")


def sig(side, entry, stop, target, tick="0.05", expires=T + timedelta(hours=1), desk=Desk.INTRADAY):
    return Signal("S", desk, "s", "v", "h", "IN:X", Intent.EXCHANGE_LIMIT, T, T, expires,
                  EvidenceTier.FIXTURE, "src", side, D(1), D(entry), D(stop), D(target), D(tick), POL)


# ---------- FB-0490 table-driven inequalities, ticks, R-multiple, fees, expiry

@pytest.mark.parametrize("side,entry,stop,target,ok", [
    (Side.BUY, "100", "99", "102", True), (Side.BUY, "100", "100", "102", False),
    (Side.BUY, "100", "99", "100", False), (Side.BUY, "100", "101", "102", False),
    (Side.SELL_SHORT, "100", "101", "98", True), (Side.SELL_SHORT, "100", "100", "98", False),
    (Side.SELL_SHORT, "100", "101", "100", False), (Side.SELL_SHORT, "100", "99", "98", False),
])
def test_stop_target_inequalities(side, entry, stop, target, ok):
    if ok:
        sig(side, entry, stop, target)
    else:
        with pytest.raises(SignalError):
            sig(side, entry, stop, target)


@pytest.mark.parametrize("price,ok", [("100.05", True), ("100.07", False), ("0", False)])
def test_tick_rounding_table(price, ok):
    if ok:
        sig(Side.BUY, price, "99", "102")
    else:
        with pytest.raises(SignalError):
            sig(Side.BUY, price, "99", "102")


@pytest.mark.parametrize("cost,expected", [("0", D(2)), ("0.5", D("1.5") / D("1.5"))])
def test_r_multiple_and_fees(cost, expected):
    assert sig(Side.BUY, "100", "99", "102").net_reward_risk(D(cost)) == expected


def test_expired_entry_cannot_be_constructed():
    with pytest.raises(SignalError):
        sig(Side.BUY, "100", "99", "102", expires=T)


# ---------- FB-0492 failure tests: stale, disconnect, halt, slippage, no liquidity, repeats, order

def oms():
    return ExchangePaperOms(max_quote_age=timedelta(seconds=5))


def order(oid="O", sig_id="S", units=10, limit="100.10"):
    return PaperOrder(oid, sig_id, Account.AUTONOMOUS, "INTRADAY", "IN:X", "BUY", units, D(limit),
                      T + timedelta(hours=1))


def test_disconnect_no_fill_and_reconnect_fills():
    m = oms(); m.submit(order())
    assert m.on_quote("O", None, T) is None
    q = Quote(D("100.00"), D("100.05"), T, T, 100, 100)
    assert m.on_quote("O", q, T + timedelta(seconds=1))


def test_high_slippage_beyond_limit_no_fill():
    m = oms(); m.submit(order(limit="100.00"))
    assert m.on_quote("O", Quote(D("100.50"), D("100.60"), T, T, 100, 100), T) is None


def test_no_liquidity_no_fill():
    m = oms(); m.submit(order())
    assert m.on_quote("O", Quote(D("100.00"), D("100.05"), T, T, 100, 0), T) is None


def test_repeated_ticks_do_not_overfill():
    m = oms(); m.submit(order(units=10))
    q = Quote(D("100.00"), D("100.05"), T, T, 1000, 1000)
    for _ in range(5):
        m.on_quote("O", q, T)
    assert m.orders["O"].filled == 10 and m.orders["O"].status is OrderStatus.FILLED


def test_out_of_order_quote_after_decision_refused():
    m = oms(); m.submit(order())
    late = Quote(D("99.00"), D("99.05"), T, T + timedelta(seconds=10), 100, 100)
    assert m.on_quote("O", late, T) is None


def test_ledger_recovery_by_replay_is_identical():
    def run():
        j = Journal(); j.contribute("c", T, "SWING", D(1000))
        j.buy("b", T, "SWING", "IN:X", D(5), D(100)); j.sell("s", T, "SWING", "IN:X", D(5), D(101))
        return j
    a, b = run(), run()
    for e in a.entries:
        b.post(e.key, e.at, e.desk, e.memo, list(e.lines))
    assert [e.key for e in a.entries] == [e.key for e in b.entries]


def test_duplicate_exit_submission_ignored():
    p = ProtectedPosition("P", True, D(10), D(100), D(97), D(106))
    bar = Bar(T, D(99), D(99), D(96), D(97))
    p.on_bar(bar); p.on_bar(bar)
    assert sum(r.kind is Event.EXIT_FILL for r in p.ledger) == 1


# ---------- FB-0493 property tests

def test_property_no_double_sell_and_cash_reconciles():
    rng = random.Random(493)
    for _ in range(200):
        p = ProtectedPosition("P", True, D(10), D(100), D(97), D(106))
        t = T
        for _ in range(20):
            o = D(rng.randrange(9000, 11000)) / 100
            h, lo = o + D(rng.randrange(0, 300)) / 100, o - D(rng.randrange(0, 300)) / 100
            p.on_bar(Bar(t, o, h, lo, o)); t += timedelta(minutes=1)
        sold = sum(r.quantity for r in p.ledger if r.kind is Event.EXIT_FILL)
        assert sold <= 10 and p.open_quantity + sold == 10
        assert all(r.at >= T for r in p.ledger)


def test_property_no_position_without_protection():
    with pytest.raises(SignalError):
        Signal("S", Desk.SWING, "s", "v", "h", "IN:X", Intent.EXCHANGE_LIMIT, T, T, T + timedelta(hours=1),
               EvidenceTier.FIXTURE, "src", Side.BUY, D(1), D(100), None, D(102), D("0.05"), POL)


# ---------- FB-1307 ETF split, distribution, halt with stop

def test_etf_split_preserves_value_and_cost():
    h = Holding("IN:ETF", D(10), inr(1000))
    out = apply_split(h, Split("IN:ETF", date(2026, 11, 17), D(10)), whole_units=True)
    assert out.holding.units == D(100) and out.holding.cost == inr(1000)


def test_halt_with_stop_keeps_risk_open():
    p = ProtectedPosition("P", True, D(10), D(100), D(97), D(106))
    p.trigger_without_price(T, "HARD_STOP")
    p.on_bar(Bar(T + timedelta(minutes=1), None, None, None, None))
    assert not p.closed


# ---------- FB-1315 ETF cash buy vs index fund subscription never share a fill

def test_etf_fill_and_mf_allotment_differ_in_time_and_price():
    m = oms(); m.submit(order())
    fill = m.on_quote("O", Quote(D("100.00"), D("100.05"), T, T, 100, 100), T)
    s = Subscription("M", "IN:MF:IDX", D(1000), T)
    s.accept(date(2026, 11, 17))
    with pytest.raises(MfOrderError):
        s.allot(PublishedNav(date(2026, 11, 17), D(50), T + timedelta(hours=12)), T)
    assert fill is not None and fill.at == T


# ---------- FB-2008 option expiry, spread widening, stale iNAV, illiquid ETF, MF overlap, lot loads

def test_expired_option_and_wide_spread_no_trade():
    c = Contract("T", "NIFTY", date(2026, 11, 16), 24000, "CE", 75, 100, 101, 1000, 1000, T, 0.5, 0, -5, 0)
    r = choose("NIFTY", 0.7, 0.2, [(1.0, 50.0)], [c], {"T"}, T, max_age=timedelta(seconds=10),
               horizon_days=0.01, iv_change=0, fees_per_lot=40)
    assert r.state is IndexSignalState.UNPRICED
    wide = Contract("T", "NIFTY", date(2026, 11, 24), 24000, "CE", 75, 90, 120, 1000, 1000, T, 0.5, 0, -5, 0)
    r = choose("NIFTY", 0.7, 0.2, [(1.0, 20.0)], [wide], {"T"}, T, max_age=timedelta(seconds=10),
               horizon_days=0.01, iv_change=0, fees_per_lot=40)
    assert r.state is IndexSignalState.NO_TRADE


def test_stale_inav_no_premium():
    q = Quote(D(100), D("100.1"), T, T, 10, 10)
    assert inav_premium(q, Inav(D(99), T - timedelta(hours=1)), max_skew=timedelta(seconds=5),
                        max_inav_age=timedelta(minutes=1), decision_at=T).fraction is None


def test_mf_overlap_summed():
    j = Journal(); j.contribute("c", T, "MUTUAL_FUND", D(2000))
    j.underlying.update({"IN:MF:A": "NIFTY", "IN:MF:B": "NIFTY"})
    for k in ("A", "B"):
        j.mf_request(f"r{k}", T, "MUTUAL_FUND", D(1000))
        j.mf_allot(f"a{k}", T, "MUTUAL_FUND", f"IN:MF:{k}", D(1000), D(100))
    exp = j.exposure_by_underlying({"IN:MF:A": Mark(D(10), T), "IN:MF:B": Mark(D(10), T)})
    assert exp == {"NIFTY": D(2000)}


# ---------- FB-2439 two index forecasts sharing exposure, duplicates, bounded capital

def test_two_index_signals_share_capital_and_dedupe():
    a = CapitalAllocator(D(100000), {"OPTIONS": D(20000)}, {"NIFTY_COMPLEX": D(15000)})
    assert a.reserve("F1:NIFTY", "OPTIONS", "NIFTY_COMPLEX", D(10000))
    assert a.reserve("F1:NIFTY", "OPTIONS", "NIFTY_COMPLEX", D(10000))      # duplicate tick batch
    assert not a.reserve("F2:BANKNIFTY", "OPTIONS", "NIFTY_COMPLEX", D(10000))
    assert a.available == D(90000)


def test_ledger_rejects_overspend_from_parallel_signals():
    j = Journal(); j.contribute("c", T, "OPTIONS", D(1000))
    j.buy("x", T, "OPTIONS", "IN:CE1", D(5), D(100))
    with pytest.raises(LedgerError):
        j.buy("y", T, "OPTIONS", "IN:CE2", D(6), D(100))
