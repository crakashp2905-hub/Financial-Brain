"""Option contracts, chain selection, spread state, IV checks and contract-level P&L (checklist 4A, 8, 9, 20).

* ``OptionContract``: exact token, CE/PE, strike, expiry, lot size, version date (FB-0476, FB-0807).
* ``select``: chain scoring on executable bid/ask, depth, OI (caveated), IV and theta per premium at
  risk; stale or one-sided quotes never score (FB-2001).
* ``Spread``: legs filled independently; entry/SL/TP on combined spread value; partial leg fills
  leave a hedge-risk flag; closing never assumes a synthetic atomic exit (FB-0478, FB-0809).
* ``block_structure``: uncovered writing and physical settlement blocked (FB-0479).
* ``monitor``: per-contract checks for spread, depth, decay and days-to-expiry (FB-0477).
* ``iv_checks``: monotone call prices in strike, convexity and calendar sanity (FB-2002).
* ``contract_pnl``: realized and unrealized premium P&L per contract with fees (FB-0905).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta


class OptionError(ValueError):
    pass


@dataclass(frozen=True)
class OptionContract:
    token: str
    underlying: str
    kind: str
    strike: float
    expiry: date
    lot_size: int
    version_from: date

    def __post_init__(self) -> None:
        if self.kind not in ("CE", "PE") or self.lot_size < 1 or self.strike <= 0:
            raise OptionError("invalid contract")


@dataclass(frozen=True)
class OptionQuote:
    bid: float
    ask: float
    bid_qty: int
    ask_qty: int
    oi: int | None
    iv: float | None
    theta: float
    at: datetime


def select(chain: list[tuple[OptionContract, OptionQuote]], now: datetime, *, kind: str, lots: int,
           max_age: timedelta, max_spread_pct: float, min_days: int) -> list[tuple[str, float, str]]:
    """Rank contracts; returns (token, score, note). Unusable contracts are excluded."""
    out = []
    for c, q in chain:
        if c.kind != kind or c.expiry < now.date() or (c.expiry - now.date()).days < min_days:
            continue
        if q.bid <= 0 or q.ask <= q.bid or now - q.at > max_age:
            continue
        mid = (q.bid + q.ask) / 2
        spread_pct = (q.ask - q.bid) / mid
        if spread_pct > max_spread_pct or q.ask_qty < lots * c.lot_size:
            continue
        decay = abs(q.theta) / q.ask
        score = -spread_pct - decay
        note = "OI unavailable" if q.oi is None else "OI is end-of-interval, not live"
        out.append((c.token, score, note))
    return sorted(out, key=lambda r: r[1], reverse=True)


ALLOWED = {"LONG_CALL", "LONG_PUT", "DEBIT_CALL_SPREAD", "DEBIT_PUT_SPREAD"}


def block_structure(structure: str, *, margin_ok: bool = False, settlement_ok: bool = False) -> str | None:
    if structure in ALLOWED:
        return None
    if not (margin_ok and settlement_ok):
        return "uncovered writing or physical settlement not modelled"
    return None


@dataclass
class LegState:
    contract: OptionContract
    side: str            # BUY or SELL
    lots: int
    filled_lots: int = 0
    avg_price: float = 0.0

    def fill(self, lots: int, price: float) -> None:
        if lots <= 0 or self.filled_lots + lots > self.lots:
            raise OptionError("leg overfill")
        self.avg_price = (self.avg_price * self.filled_lots + price * lots) / (self.filled_lots + lots)
        self.filled_lots += lots


@dataclass
class Spread:
    legs: list[LegState]
    stop_value: float
    target_value: float
    events: list[str] = field(default_factory=list)

    @property
    def hedged(self) -> bool:
        return len({leg.filled_lots for leg in self.legs}) == 1

    def value(self, marks: dict[str, float]) -> float:
        """Combined per-unit spread value (long legs positive, short legs negative)."""
        v = 0.0
        for leg in self.legs:
            sign = 1 if leg.side == "BUY" else -1
            v += sign * marks[leg.contract.token]
        return v

    def check(self, exit_marks: dict[str, float]) -> str | None:
        """Trigger on combined value at exit-side marks; unhedged state is flagged first."""
        if not self.hedged:
            self.events.append("HEDGE_RISK")
            return "HEDGE_RISK"
        v = self.value(exit_marks)
        if v <= self.stop_value:
            return "STOP"
        if v >= self.target_value:
            return "TARGET"
        return None

    def close_orders(self) -> list[tuple[str, str, int]]:
        """Each leg closed for its own filled quantity; no synthetic atomic exit."""
        return [(leg.contract.token, "SELL" if leg.side == "BUY" else "BUY", leg.filled_lots)
                for leg in self.legs if leg.filled_lots]


def monitor(c: OptionContract, q: OptionQuote, now: datetime, *, max_spread_pct: float,
            min_depth_lots: int, min_days: int) -> list[str]:
    flags = []
    if q.bid <= 0:
        flags.append("NO_BID")
    else:
        if (q.ask - q.bid) / ((q.ask + q.bid) / 2) > max_spread_pct:
            flags.append("WIDE_SPREAD")
    if q.bid_qty < min_depth_lots * c.lot_size:
        flags.append("THIN_DEPTH")
    if (c.expiry - now.date()).days < min_days:
        flags.append("NEAR_EXPIRY")
    if q.bid > 0 and abs(q.theta) / q.bid > 0.1:
        flags.append("FAST_DECAY")
    return flags


def iv_checks(calls_by_strike: dict[float, float], near_atm_iv: float | None, far_atm_iv: float | None,
              t_near: float, t_far: float) -> list[str]:
    """No-arbitrage diagnostics for one expiry plus a calendar total-variance check."""
    problems = []
    ks = sorted(calls_by_strike)
    px = [calls_by_strike[k] for k in ks]
    if any(b > a for a, b in zip(px, px[1:], strict=False)):
        problems.append("call price increases with strike")
    for i in range(1, len(ks) - 1):
        k0, k1, k2 = ks[i - 1], ks[i], ks[i + 1]
        w = (k2 - k1) / (k2 - k0)
        if px[i] > w * px[i - 1] + (1 - w) * px[i + 1] + 1e-9:
            problems.append(f"convexity violated at {k1}")
    if near_atm_iv is not None and far_atm_iv is not None and far_atm_iv ** 2 * t_far < near_atm_iv ** 2 * t_near:
        problems.append("calendar total variance decreasing")
    return problems


def contract_pnl(fills: list[tuple[str, int, float]], lot_size: int, mark_bid: float | None,
                 fees: float) -> dict[str, float | None]:
    """``fills`` are (BUY|SELL, lots, premium). Unrealized uses the exit-side bid; None if no bid."""
    pos, cost, realized = 0, 0.0, 0.0
    for side, lots, px in fills:
        if side == "BUY":
            cost += lots * px
            pos += lots
        else:
            if lots > pos:
                raise OptionError("sell exceeds long position")
            avg = cost / pos
            realized += lots * (px - avg) * lot_size
            cost -= avg * lots
            pos -= lots
    unreal = None if pos and mark_bid is None else (pos * (mark_bid or 0) * lot_size - cost * lot_size)
    return {"realized": realized - fees, "unrealized": unreal, "open_lots": float(pos)}
