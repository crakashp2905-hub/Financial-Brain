"""The intraday paper-trading engine: fixed rules, fed prices, producing paper trades. No orders exist here.

One :class:`DayEngine` per trading day. It is fed the opening range at 09:30 and then a stream of prices; it
opens and closes **paper** trades by rules fixed in advance and versioned (:data:`VERSION`), charges the full
intraday cost schedule, and keeps every trade - winners, losers and the ones it skipped and why. It does not
know whether it is being fed live Kite quotes or a replay of minute candles; the runner decides that and
labels the result.

## Strategies in version ``live-v1`` (pre-registered in ``PRE-REGISTERED live paper trading v1``)

    ORB   opening-range breakout, long and short. Range = 09:15-09:29. From 09:30 to 11:00 the first price
          beyond the range opens a trade in its direction. Stop: the opposite end of the range. Target: 2R.
          Time exit 15:15. The backtest of exactly this (251,000 trades) lost 15-21 bp a trade after costs.
    GAP   gap fade. At 09:30, if the open is >= 1.5% above the previous close and the price is back below the
          open, short; mirror image for a gap down. Stop: the day's extreme so far. Target: the previous close.
          Time exit 15:15. Not backtested - this is its first test.

## Sizing (the plan's intraday rules)

Risk ``RISK_PER_TRADE`` of the paper account to the stop: quantity = risk / (entry - stop distance), capped at
``MAX_WEIGHT`` of the account per trade, and no new trade once open intraday exposure reaches ``MAX_GROSS`` x the
account (intraday margin). One trade per strategy per symbol per day.

The price that fills an entry or exit is the price the engine was given, so a feed sampled every 20 seconds
fills at the sampled price, not the exact trigger: stops are realistically *worse* than the trigger.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time

from ..costs.india import CostModel, Segment

VERSION = "live-v1"
ACCOUNT_INR = 1_000_000.0
RISK_PER_TRADE = 0.0025
MAX_WEIGHT = 0.10
MAX_GROSS = 5.0
OR_END = time(9, 30)
TRIGGER_END = time(11, 0)
TIME_EXIT = time(15, 15)
TARGET_R = 2.0
GAP_MIN = 0.015
STRATEGIES = ("ORB", "GAP")


class EngineError(ValueError):
    pass


@dataclass
class SymbolDay:
    symbol: str
    prev_close: float
    bucket: str = "large"
    open: float | None = None
    or_hi: float | None = None
    or_lo: float | None = None
    day_hi: float | None = None
    day_lo: float | None = None
    done: set = field(default_factory=set)        # strategies already traded today


@dataclass
class Trade:
    trade_id: str
    day: str
    strategy: str
    symbol: str
    side: str
    qty: int
    entry_ts: str
    entry_px: float
    stop: float
    target: float
    version: str = VERSION
    exit_ts: str | None = None
    exit_px: float | None = None
    exit_reason: str | None = None
    gross_pnl: float = 0.0
    costs: float = 0.0
    net_pnl: float = 0.0
    r_multiple: float = 0.0
    bucket: str = "large"

    @property
    def is_open(self) -> bool:
        return self.exit_ts is None

    @property
    def risk_inr(self) -> float:
        return abs(self.entry_px - self.stop) * self.qty


class DayEngine:
    def __init__(self, day: str, *, account: float = ACCOUNT_INR, risk: float = RISK_PER_TRADE,
                 costs: CostModel | None = None, strategies=STRATEGIES):
        self.day = day
        self.account = account
        self.risk = risk
        self.costs = costs or CostModel()
        self.strategies = tuple(strategies)
        self.symbols: dict[str, SymbolDay] = {}
        self.trades: list[Trade] = []
        self.skipped: list[dict] = []
        self.closed_for_day = False

    # ----------------------------------------------------------------- setup
    def add_symbol(self, symbol: str, prev_close: float, bucket: str = "large") -> None:
        if prev_close <= 0:
            raise EngineError(f"{symbol}: previous close must be positive")
        self.symbols[symbol] = SymbolDay(symbol, prev_close, bucket)

    def set_opening(self, symbol: str, *, open_px: float, or_hi: float, or_lo: float) -> None:
        s = self.symbols[symbol]
        if not (or_lo <= open_px <= or_hi) or or_lo <= 0:
            raise EngineError(f"{symbol}: open {open_px} outside its own range {or_lo}-{or_hi}")
        s.open, s.or_hi, s.or_lo, s.day_hi, s.day_lo = open_px, or_hi, or_lo, or_hi, or_lo

    # ----------------------------------------------------------------- the stream
    def open_exposure(self) -> float:
        return sum(t.entry_px * t.qty for t in self.trades if t.is_open)

    def on_price(self, symbol: str, ts: datetime, price: float) -> list[Trade]:
        """Feed one price. Returns the trades opened or closed by it."""
        if self.closed_for_day or symbol not in self.symbols or price <= 0:
            return []
        s = self.symbols[symbol]
        if s.open is None:
            return []
        s.day_hi, s.day_lo = max(s.day_hi, price), min(s.day_lo, price)
        changed = []
        for t in [t for t in self.trades if t.is_open and t.symbol == symbol]:
            why = self._exit_reason(t, price, ts)
            if why:
                self._close(t, ts, price, why)
                changed.append(t)
        clock = ts.time()
        if OR_END <= clock <= TRIGGER_END:
            for strat in self.strategies:
                if strat in s.done:
                    continue
                sig = self._signal(strat, s, price, clock)
                if sig:
                    t = self._open(strat, s, sig, ts, price)
                    if t:
                        changed.append(t)
        return changed

    def end_of_day(self, ts: datetime, prices: dict[str, float]) -> list[Trade]:
        out = []
        for t in self.trades:
            if t.is_open:
                px = prices.get(t.symbol) or t.entry_px
                self._close(t, ts, px, "time")
                out.append(t)
        self.closed_for_day = True
        return out

    # ----------------------------------------------------------------- rules
    def _signal(self, strat: str, s: SymbolDay, price: float, clock: time):
        if strat == "ORB":
            if price > s.or_hi:
                return ("long", s.or_lo)
            if price < s.or_lo:
                return ("short", s.or_hi)
            return None
        if strat == "GAP":
            gap = s.open / s.prev_close - 1.0
            if gap >= GAP_MIN and price < s.open:
                return ("short", s.day_hi)
            if gap <= -GAP_MIN and price > s.open:
                return ("long", s.day_lo)
            if abs(gap) < GAP_MIN:
                s.done.add("GAP")          # no gap today: nothing to fade
            return None
        raise EngineError(f"unknown strategy {strat!r}")

    def _open(self, strat: str, s: SymbolDay, sig, ts: datetime, price: float):
        side, stop = sig
        per_share = (price - stop) if side == "long" else (stop - price)
        if per_share <= 0:
            s.done.add(strat)
            self.skipped.append({"symbol": s.symbol, "strategy": strat, "why": "price already beyond its stop"})
            return None
        qty = int(min(self.risk * self.account / per_share, MAX_WEIGHT * self.account / price))
        if qty < 1:
            s.done.add(strat)
            self.skipped.append({"symbol": s.symbol, "strategy": strat, "why": "one share is more than the risk budget"})
            return None
        if self.open_exposure() + qty * price > MAX_GROSS * self.account:
            self.skipped.append({"symbol": s.symbol, "strategy": strat, "why": "intraday exposure limit reached"})
            return None                    # not marked done: may open later when exposure frees up
        if strat == "GAP":
            target = s.prev_close
        else:
            target = price + TARGET_R * per_share if side == "long" else price - TARGET_R * per_share
        t = Trade(trade_id=f"{self.day}-{strat}-{s.symbol}", day=self.day, strategy=strat, symbol=s.symbol,
                  side=side, qty=qty, entry_ts=ts.isoformat(), entry_px=price, stop=stop, target=target,
                  bucket=s.bucket)
        self.trades.append(t)
        s.done.add(strat)
        return t

    @staticmethod
    def _exit_reason(t: Trade, price: float, ts: datetime) -> str | None:
        if ts.time() >= TIME_EXIT:
            return "time"
        if t.side == "long":
            if price <= t.stop:
                return "stop"
            if price >= t.target:
                return "target"
        else:
            if price >= t.stop:
                return "stop"
            if price <= t.target:
                return "target"
        return None

    def _close(self, t: Trade, ts: datetime, price: float, why: str) -> None:
        t.exit_ts, t.exit_px, t.exit_reason = ts.isoformat(), price, why
        sign = 1 if t.side == "long" else -1
        t.gross_pnl = sign * (price - t.entry_px) * t.qty
        rt = self.costs.round_trip(turnover=t.entry_px * t.qty, segment=Segment.INTRADAY, bucket=t.bucket)
        t.costs = rt["total"]
        t.net_pnl = t.gross_pnl - t.costs
        t.r_multiple = t.gross_pnl / t.risk_inr if t.risk_inr else 0.0

    # ----------------------------------------------------------------- summary
    def summary(self) -> dict:
        out = {}
        for strat in self.strategies:
            ts = [t for t in self.trades if t.strategy == strat and not t.is_open]
            if not ts:
                out[strat] = {"trades": 0}
                continue
            out[strat] = {"trades": len(ts), "wins": sum(1 for t in ts if t.net_pnl > 0),
                          "gross": sum(t.gross_pnl for t in ts), "costs": sum(t.costs for t in ts),
                          "net": sum(t.net_pnl for t in ts), "mean_r": sum(t.r_multiple for t in ts) / len(ts),
                          "by_exit": {k: sum(1 for t in ts if t.exit_reason == k) for k in ("target", "stop", "time")}}
        return out
