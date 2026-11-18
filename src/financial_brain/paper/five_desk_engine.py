"""End-to-end five-desk paper engine (FB-0801, FB-0810, FB-0813, D3/D4 acceptance).

One path for every exchange desk:

    Signal (validated contract) -> admissibility receipt -> capital reservation -> paper order
    -> quote-backed fill -> journal + position ledger + armed SL/TP -> bars -> exit fill
    -> journal + position ledger -> capital released

Every step is keyed, so re-running the same inputs after a crash posts nothing twice (outbox
semantics). Nothing here can reach a broker. An order is verified against cash, the reservation,
signal expiry and the instrument identity before it is committed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from financial_brain.execution.exchange_paper import Account, ExchangePaperOms, PaperOrder
from financial_brain.instruments.pit import Quote
from financial_brain.portfolio.multi_asset_ledger import Journal
from financial_brain.portfolio.position_ledger import Ev, PositionLedger, Record
from financial_brain.risk.capital_allocator import CapitalAllocator
from financial_brain.signals.admissibility import Costs, Decision, Receipt, Scenario, decide
from financial_brain.signals.contracts import EXCHANGE_INTENTS, Side, Signal
from financial_brain.signals.exit_triggers import Bar, Event, ProtectedPosition


class EngineError(ValueError):
    pass


@dataclass
class Engine:
    journal: Journal
    allocator: CapitalAllocator
    oms: ExchangePaperOms
    positions: PositionLedger = field(default_factory=PositionLedger)
    protected: dict[str, ProtectedPosition] = field(default_factory=dict)
    receipts: dict[str, Receipt] = field(default_factory=dict)
    outbox: set[str] = field(default_factory=set)
    known_instruments: set[str] = field(default_factory=set)
    underlying: dict[str, str] = field(default_factory=dict)

    def _rec(self, key: str, pid: str, inst: str, kind: Ev, at: datetime, **snap: object) -> None:
        self.positions.append(Record(key, pid, Account.AUTONOMOUS.value, inst, kind, at,
                                     tuple(sorted((k, str(v)) for k, v in snap.items()))))

    def consider(self, s: Signal, scenario: Scenario | None, costs: Costs | None, *, quote_ok: bool,
                 spread_ok: bool, halted: bool, hurdle: Decimal, min_samples: int, now: datetime) -> Receipt:
        """Decision receipt and, for TRADE, a capital reservation and a paper order."""
        key = f"consider:{s.signal_id}"
        if key in self.outbox:
            return self.receipts[s.signal_id]
        if s.intent not in EXCHANGE_INTENTS or s.side is not Side.BUY:
            raise EngineError("engine handles long exchange entries; MF and shorts use their own paths")
        if s.instrument_id not in self.known_instruments:
            raise EngineError(f"unknown instrument {s.instrument_id}")
        assert s.quantity is not None and s.entry_limit is not None
        notional = s.quantity * s.entry_limit
        budget_ok = self.allocator.reserve(f"res:{s.signal_id}", s.desk.value,
                                           self.underlying.get(s.instrument_id, s.instrument_id), notional)
        r = decide(scenario, costs, units=int(s.quantity), quote_ok=quote_ok, spread_ok=spread_ok,
                   expired=now >= s.expires_at, halted=halted, budget_ok=budget_ok,
                   hurdle_per_unit=hurdle, min_samples=min_samples)
        if r.decision is not Decision.TRADE and budget_ok:
            self.allocator.release(f"res:{s.signal_id}")
        self.receipts[s.signal_id] = r
        self.outbox.add(key)
        self._rec(f"{s.signal_id}:sig", s.signal_id, s.instrument_id, Ev.ENTRY_SIGNAL, now,
                  stop=s.stop_price, target=s.target_price, entry=s.entry_limit, decision=r.decision.value)
        if r.decision is Decision.TRADE:
            self.oms.submit(PaperOrder(f"{s.signal_id}:entry", s.signal_id, Account.AUTONOMOUS, s.desk.value,
                                       s.instrument_id, "BUY", int(s.quantity), s.entry_limit, s.expires_at))
            self._rec(f"{s.signal_id}:intent", s.signal_id, s.instrument_id, Ev.ENTRY_INTENT, now)
        return r

    def on_quote(self, s: Signal, quote: Quote | None, now: datetime) -> bool:
        """Try to fill the entry; on a fill, post to the journal and arm protection. Idempotent."""
        oid = f"{s.signal_id}:entry"
        if oid not in self.oms.orders or f"fill:{oid}" in self.outbox:
            return False
        f = self.oms.on_quote(oid, quote, now)
        o = self.oms.orders[oid]
        if f is None or o.remaining:
            return False
        units = Decimal(o.filled)
        avg = sum((Decimal(x.units) * x.price for x in o.fills), Decimal(0)) / units
        if units * avg > self.journal.cash("AVAILABLE", s.desk.value):
            raise EngineError("fill would exceed available cash")
        self.journal.buy(f"j:{oid}", now, s.desk.value, s.instrument_id, units, avg)
        assert s.stop_price is not None and s.target_price is not None
        p = ProtectedPosition(s.signal_id, True, units, avg, s.stop_price, s.target_price)
        p.arm(now)
        self.protected[s.signal_id] = p
        self._rec(f"{s.signal_id}:fill", s.signal_id, s.instrument_id, Ev.ENTRY_FILL, now, price=avg, units=units)
        self._rec(f"{s.signal_id}:armed", s.signal_id, s.instrument_id, Ev.STOP_TP_ARMED, now)
        self.outbox.add(f"fill:{oid}")
        return True

    def on_bar(self, s: Signal, bar: Bar) -> bool:
        """Evaluate exits; a filled exit posts once to journal and position ledger, then frees capital."""
        p = self.protected.get(s.signal_id)
        if p is None or f"exit:{s.signal_id}" in self.outbox:
            return False
        p.on_bar(bar)
        fills = [r for r in p.ledger if r.kind is Event.EXIT_FILL]
        if not fills:
            return False
        f = fills[0]
        assert f.price is not None and f.quantity is not None
        self.journal.sell(f"j:{s.signal_id}:exit", f.at, s.desk.value, s.instrument_id, f.quantity, f.price)
        self._rec(f"{s.signal_id}:trig", s.signal_id, s.instrument_id, Ev.EXIT_TRIGGER, f.at, reason=f.reason)
        self._rec(f"{s.signal_id}:xfill", s.signal_id, s.instrument_id, Ev.EXIT_FILL, f.at, price=f.price)
        self._rec(f"{s.signal_id}:closed", s.signal_id, s.instrument_id, Ev.POSITION_CLOSED, f.at)
        self.allocator.release(f"res:{s.signal_id}")
        self.outbox.add(f"exit:{s.signal_id}")
        return True

    def settle(self, desk: str, at: datetime, lag: timedelta, now: datetime) -> None:
        """Release unsettled proceeds only after the settlement lag (FB-0911 policy hook)."""
        if now - at >= lag:
            amt = self.journal.cash("UNSETTLED", desk)
            if amt > 0:
                self.journal.settle(f"settle:{desk}:{at.isoformat()}", now, desk, amt)
