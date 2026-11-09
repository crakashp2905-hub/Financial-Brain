"""End-to-end paper pilot: opening-range breakout on a SYNTHETIC intraday tape (blueprint section 4 pilot).

Chain: source bars -> opening range (frozen after its window) -> breakout on a completed bar -> prerequisite gate
-> central risk gate -> OMS intent (idempotent) -> next-open fill with half-spread -> double-entry ledger ->
stop / target / time exit -> JSONL trace. Everything is generated locally; nothing is sent anywhere.

Every trace row names its stage, so one trade can be reconstructed from source to cash.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from financial_brain.kernel.events import EventKernel
from financial_brain.kernel.fills import OhlcBar, resolve_stop_target
from financial_brain.kernel.guards import prerequisite_gate
from financial_brain.kernel.ledger import CREDIT, DEBIT, DoubleEntryLedger, Entry, Transaction
from financial_brain.kernel.oms import Oms, State
from financial_brain.kernel.risk import Latch, MarketState, OrderRequest, Policy, evaluate
from financial_brain.kernel.units import Money

IST = timezone(timedelta(hours=5, minutes=30))
OPEN_RANGE_BARS = 15
EXIT_TIME = time(15, 15)
SPREAD = Decimal("0.10")
COMMISSION_BPS = Decimal("3")
CURRENCY = "INR"


@dataclass(frozen=True)
class Bar:
    ts: datetime  # tz-aware
    o: float
    h: float
    l: float  # noqa: E741 - mirrors OHLC naming
    c: float
    v: float

    def ohlc(self) -> OhlcBar:
        return OhlcBar(self.o, self.h, self.l, self.c, self.v)


def synthetic_tape(seed: int, *, drift: float, start: float = 500.0, day: tuple[int, int, int] = (2026, 11, 9)) -> list[Bar]:
    """Deterministic SYNTHETIC 1-minute tape from 09:15 IST, 375 bars. Labelled synthetic everywhere it is used."""
    rng = random.Random(seed)
    base = datetime(*day, 9, 15, tzinfo=IST)
    price, bars = start, []
    for i in range(375):
        o = price
        c = max(0.05, o * (1 + drift + rng.gauss(0, 0.002)))
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.0007)))
        l = min(o, c) * (1 - abs(rng.gauss(0, 0.0007)))  # noqa: E741
        bars.append(Bar(base + timedelta(minutes=i), round(o, 2), round(h, 2), round(l, 2), round(c, 2), 50_000.0))
        price = c
    return bars


@dataclass
class PilotResult:
    trace: list[dict[str, Any]] = field(default_factory=list)
    outcome: str = "NO_TRADE"
    pnl: Decimal = Decimal(0)
    ledger: DoubleEntryLedger = field(default_factory=DoubleEntryLedger)
    submissions: int = 0

    def jsonl(self) -> str:
        return "\n".join(json.dumps(r, sort_keys=True, default=str) for r in self.trace) + "\n"


def _paise(value: Decimal) -> Decimal:
    """Money at the ledger boundary rounds to paise (half-even), so statements match exactly."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def run_pilot(
    bars: list[Bar],
    *,
    symbol: str = "ABC",
    equity: Decimal = Decimal("1000000"),
    risk_per_trade: Decimal = Decimal("5000"),
    kernel: EventKernel | None = None,
    tick: Decimal = Decimal("0.05"),
) -> PilotResult:
    res = PilotResult()
    kernel = kernel or EventKernel()
    trace = res.trace.append
    if len(bars) < OPEN_RANGE_BARS + 2:
        trace({"stage": "gate", "decision": "NO_TRADE", "missing": ["insufficient bars"]})
        return res
    local = [b.ts.astimezone(IST) for b in bars]
    rng_high = max(b.h for b in bars[:OPEN_RANGE_BARS])
    rng_low = min(b.l for b in bars[:OPEN_RANGE_BARS])
    trace({"stage": "opening_range", "high": rng_high, "low": rng_low, "complete_at": local[OPEN_RANGE_BARS - 1].isoformat(),
           "synthetic": True})
    oms = Oms({"paper": Decimal("10000000")})
    policy = Policy(Decimal("2000000"), 100000, Decimal("5000000"), Decimal("0.10"), Decimal("50000"), 300)
    entry_index = None
    for i in range(OPEN_RANGE_BARS, len(bars) - 1):
        if local[i].time() >= EXIT_TIME:
            break
        if bars[i].c > rng_high:
            entry_index = i
            break
    if entry_index is None:
        trace({"stage": "signal", "decision": "NO_TRADE", "reason": "NO_BREAKOUT_BEFORE_EXIT_WINDOW"})
        return res
    signal = bars[entry_index]
    fill_i = entry_index + 1
    fill_bar = bars[fill_i]
    trace({"stage": "signal", "bar": signal.ts.isoformat(), "close": signal.c, "range_high": rng_high,
           "fill_bar": fill_bar.ts.isoformat()})
    entry_px = Decimal(str(fill_bar.o)) + SPREAD / 2
    stop = Decimal(str(rng_low))
    risk_per_unit = entry_px - stop
    if risk_per_unit <= 0:
        trace({"stage": "gate", "decision": "NO_TRADE", "missing": ["stop above entry"]})
        return res
    qty = int(risk_per_trade // risk_per_unit)
    gate = prerequisite_gate({
        "instrument_tradable": True,
        "tick_known": tick > 0,
        "range_complete": True,
        "quote_fresh": True,
        "size_positive": qty > 0,
    })
    trace({"stage": "gate", "decision": gate.decision, "missing": list(gate.missing)})
    if not gate.tradable:
        return res
    notional = _paise(entry_px * qty)
    state =MarketState(equity, equity, Decimal(0), Decimal(0), {}, {}, 0, True)
    verdict = evaluate(policy, OrderRequest(symbol, "BUY", qty, entry_px), state, Latch())
    trace({"stage": "risk", **verdict})
    if verdict["decision"] != "ALLOW":
        res.outcome = "RISK_DENIED"
        return res
    entry_key = f"entry:{symbol}:{bars[0].ts.date().isoformat()}"

    def _submit() -> None:
        res.submissions += 1
        oms.submit_intent(client_id=entry_key, account="paper", side="BUY", quantity=qty,
                          limit=None, reserve=notional)
        oms.move(entry_key, State.SUBMITTED)
        oms.move(entry_key, State.ACCEPTED)
        oms.record_fill(entry_key, qty, entry_px)

    kernel.side_effect(entry_key, _submit)  # at most once, even across a resume
    fees = _paise(notional * COMMISSION_BPS / Decimal(10_000))
    res.ledger.post(Transaction(f"{entry_key}:buy", (
        Entry("position:" + symbol, DEBIT, Money.of(notional, CURRENCY)),
        Entry("cash", CREDIT, Money.of(notional, CURRENCY)),
    )))
    res.ledger.post(Transaction(f"{entry_key}:fee", (
        Entry("fees", DEBIT, Money.of(fees, CURRENCY)),
        Entry("cash", CREDIT, Money.of(fees, CURRENCY)),
    )))
    trace({"stage": "fill", "order": entry_key, "qty": qty, "price": str(entry_px), "fees": str(fees),
           "fidelity": "BAR", "mode": "PAPER_SIMULATED"})
    target = entry_px + 2 * risk_per_unit
    exit_px, reason, exit_bar = None, None, None
    for j in range(fill_i, len(bars)):
        b = bars[j]
        if local[j].time() >= EXIT_TIME:
            exit_px, reason, exit_bar = Decimal(str(b.o)), "TIME_EXIT", j
            break
        outcome, price = resolve_stop_target(b.ohlc(), "BUY", float(stop), float(target))
        if outcome != "NONE":
            exit_px, reason, exit_bar = Decimal(str(price)), outcome, j
            break
    if exit_px is None or exit_bar is None:
        exit_px, reason, exit_bar = Decimal(str(bars[-1].c)), "END_OF_DATA", len(bars) - 1
    exit_fees = _paise(exit_px * qty * COMMISSION_BPS / Decimal(10_000))
    proceeds = _paise(exit_px * qty)
    pnl_gross = proceeds - notional
    entries = [
        Entry("cash", DEBIT, Money.of(proceeds - exit_fees, CURRENCY)),
        Entry("fees", DEBIT, Money.of(exit_fees, CURRENCY)),
        Entry("position:" + symbol, CREDIT, Money.of(notional, CURRENCY)),
    ]
    if pnl_gross >= 0:
        entries.append(Entry("realized_pnl", CREDIT, Money.of(pnl_gross, CURRENCY)))
    else:
        entries.append(Entry("realized_pnl", DEBIT, Money.of(-pnl_gross, CURRENCY)))
    res.ledger.post(Transaction(f"{entry_key}:exit", tuple(entries)))
    oms_exit = f"exit:{symbol}:{bars[0].ts.date().isoformat()}"
    oms.submit_intent(client_id=oms_exit, account="paper", side="SELL", quantity=qty, limit=None,
                      reserve=Decimal(0))
    oms.move(oms_exit, State.SUBMITTED)
    oms.move(oms_exit, State.ACCEPTED)
    oms.record_fill(oms_exit, qty, exit_px)
    trace({"stage": "exit", "reason": reason, "bar": bars[exit_bar].ts.isoformat(), "price": str(exit_px),
           "gross_pnl": str(pnl_gross), "fees": str(fees + exit_fees)})
    res.pnl = pnl_gross - fees - exit_fees
    res.outcome = "WIN" if res.pnl > 0 else "LOSS"
    trace({"stage": "ledger", "net_pnl": str(res.pnl), "balanced": res.ledger.trial_balance_ok(),
           "cash_balance": str(res.ledger.balance("cash", CURRENCY))})
    return res


def cash_equals_net_pnl(res: PilotResult) -> bool:
    return res.ledger.balance("cash", CURRENCY) == res.pnl


__all__ = ["Bar", "IST", "PilotResult", "cash_equals_net_pnl", "run_pilot", "synthetic_tape"]
