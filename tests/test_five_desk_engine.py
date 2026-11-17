"""End-to-end paper path: signal -> receipt -> capital -> order -> fill -> ledger -> exit -> release."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.execution.exchange_paper import ExchangePaperOms
from financial_brain.instruments.pit import Quote
from financial_brain.paper.five_desk_engine import Engine, EngineError
from financial_brain.portfolio.multi_asset_ledger import Journal
from financial_brain.portfolio.position_ledger import Ev
from financial_brain.risk.capital_allocator import CapitalAllocator
from financial_brain.signals.admissibility import Costs, Decision, Scenario
from financial_brain.signals.contracts import Desk, EvidenceTier, ExitPolicy, ExitTrigger, Intent, Side, Signal
from financial_brain.signals.exit_triggers import Bar

T = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)
POL = ExitPolicy((ExitTrigger.HARD_STOP, ExitTrigger.TAKE_PROFIT, ExitTrigger.TIME_STOP), "v1")
SCEN = Scenario(D("0.45"), D("0.35"), D(6), D(3), D(0), D(0), True, 500)
COST = Costs(D("0.3"), "c1")


def sig(sid="S1", qty=10):
    return Signal(sid, Desk.SWING, "swing5", "v1", "h", "IN:A", Intent.EXCHANGE_LIMIT, T, T,
                  T + timedelta(hours=6), EvidenceTier.LOCAL_PAPER, "src", Side.BUY, D(qty),
                  D("100.00"), D("97.00"), D("106.00"), D("0.05"), POL)


def engine():
    j = Journal(); j.contribute("c", T, "SWING", D(10000))
    e = Engine(j, CapitalAllocator(D(10000), {"SWING": D(5000)}), ExchangePaperOms(timedelta(seconds=30)))
    e.known_instruments.add("IN:A")
    return e


def q(at):
    return Quote(D("99.95"), D("100.00"), at, at, 1000, 1000)


def consider(e, s):
    return e.consider(s, SCEN, COST, quote_ok=True, spread_ok=True, halted=False, hurdle=D("0.1"),
                      min_samples=100, now=T)


def test_full_round_trip_reconciles():
    e, s = engine(), sig()
    assert consider(e, s).decision is Decision.TRADE
    assert e.allocator.available == D(9000)
    assert e.on_quote(s, q(T + timedelta(seconds=1)), T + timedelta(seconds=1))
    assert e.journal.cash("AVAILABLE", "SWING") == D(9000)
    assert e.on_bar(s, Bar(T + timedelta(days=1), D(101), D(107), D(100), D(106)))
    e.journal.check()
    assert e.journal.cash("UNSETTLED", "SWING") == D(1060)
    assert e.allocator.available == D(10000)
    kinds = [r.kind for r in e.positions.history("S1")]
    assert kinds == [Ev.ENTRY_SIGNAL, Ev.ENTRY_INTENT, Ev.ENTRY_FILL, Ev.STOP_TP_ARMED,
                     Ev.EXIT_TRIGGER, Ev.EXIT_FILL, Ev.POSITION_CLOSED]
    e.settle("SWING", T + timedelta(days=1), timedelta(days=1), T + timedelta(days=2))
    assert e.journal.cash("AVAILABLE", "SWING") == D(10060)


def test_replay_after_crash_posts_nothing_twice():
    e, s = engine(), sig()
    for _ in range(2):
        consider(e, s)
        e.on_quote(s, q(T + timedelta(seconds=1)), T + timedelta(seconds=1))
        e.on_bar(s, Bar(T + timedelta(days=1), D(99), D(99), D(96), D(97)))
    assert len(e.journal.entries) == 3 and e.journal.balance("INCOME:REALIZED", "SWING") == D(30)


def test_rejected_signal_releases_capital_and_never_orders():
    e, s = engine(), sig()
    r = e.consider(s, SCEN, None, quote_ok=True, spread_ok=True, halted=False, hurdle=D("0.1"),
                   min_samples=100, now=T)
    assert r.decision is Decision.REJECT and e.allocator.available == D(10000) and not e.oms.orders


def test_budget_denied_is_watch():
    e = engine()
    r = consider(e, sig(qty=60))
    assert r.decision is Decision.WATCH and not e.oms.orders


def test_unknown_instrument_refused():
    e = engine(); e.known_instruments.clear()
    with pytest.raises(EngineError):
        consider(e, sig())


def test_no_fill_without_quote_and_no_exit_without_fill():
    e, s = engine(), sig()
    consider(e, s)
    assert not e.on_quote(s, None, T)
    assert not e.on_bar(s, Bar(T, D(90), D(91), D(80), D(85)))
