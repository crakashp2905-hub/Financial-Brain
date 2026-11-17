"""Desk logic building blocks (FB-0473, FB-0503, FB-0608, FB-1602, FB-1605, FB-1703, FB-1901, FB-1903).

* ``FrozenExitPolicy``: one versioned API for fixed, ATR, trailing, staged and time exits.
* ``hit_probabilities``: SL/TP/time-exit probabilities by simulation from *pre-decision* volatility,
  with a gap term; seeded and reproducible.
* ``price_constraints``: entry zone, max spread, max slippage and TTL checks.
* ``swing_candidates``: up to five daily BUY candidates with ATR-based SL/TP; may be empty.
* ``rotation``: equal-volatility sleeve weights with cash allowed.
* ``simulate_sip``: lump-sum vs SIP vs rebalance on dated NAVs with cut-off lag.
* ``overlap``: exposure grouped by sector/index/factor tags.
* ``exit_guard``: reduce positions when aggregate risk breaches a limit.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta


class DeskError(ValueError):
    pass


@dataclass(frozen=True)
class FrozenExitPolicy:
    version: str
    kind: str                         # FIXED, ATR, TRAILING, STAGED, TIME
    stop_mult: float = 0.0
    target_mult: float = 0.0
    trail: float = 0.0
    stages: tuple[tuple[float, float], ...] = ()
    max_bars: int = 0

    def levels(self, entry: float, atr: float, fixed_stop: float | None = None,
               fixed_target: float | None = None) -> tuple[float, float]:
        if self.kind == "FIXED":
            if fixed_stop is None or fixed_target is None:
                raise DeskError("fixed policy needs explicit levels")
            return fixed_stop, fixed_target
        if atr <= 0:
            raise DeskError("ATR must be positive")
        return entry - self.stop_mult * atr, entry + self.target_mult * atr


def hit_probabilities(entry: float, stop: float, target: float, daily_vol: float, bars: int,
                      gap_prob: float, gap_size: float, n: int = 4000, seed: int = 11) -> dict[str, float]:
    if not stop < entry < target:
        raise DeskError("long plan needs stop < entry < target")
    rng = random.Random(seed)
    tp = sl = te = 0
    gap_loss = 0.0
    for _ in range(n):
        px = entry
        for _ in range(bars):
            if rng.random() < gap_prob:
                px *= 1 - gap_size
            else:
                px *= 1 + rng.gauss(0, daily_vol)
            if px <= stop:
                sl += 1
                gap_loss += max(stop - px, 0.0)
                break
            if px >= target:
                tp += 1
                break
        else:
            te += 1
    return {"p_target": tp / n, "p_stop": sl / n, "p_time": te / n,
            "mean_gap_beyond_stop": gap_loss / max(sl, 1)}


def price_constraints(*, entry_low: float, entry_high: float, bid: float, ask: float,
                      max_spread_bps: float, max_slip_bps: float, decided_at: datetime,
                      ttl: timedelta, now: datetime) -> list[str]:
    why = []
    if now > decided_at + ttl:
        why.append("TTL expired: re-evaluate, never fill late")
    if (ask - bid) / ((ask + bid) / 2) * 1e4 > max_spread_bps:
        why.append("spread too wide")
    if ask > entry_high * (1 + max_slip_bps / 1e4):
        why.append("price above entry zone plus slippage")
    if ask < entry_low * (1 - max_slip_bps / 1e4):
        why.append("price far below entry zone: re-evaluate")
    return why


@dataclass(frozen=True)
class Candidate:
    symbol: str
    score: float
    entry: float
    stop: float
    target: float
    net_ev: float


def swing_candidates(rows: list[dict], policy: FrozenExitPolicy, cost: float, k: int = 5) -> list[Candidate]:
    """Rows carry symbol, score, close, atr, p_target, p_stop. Only positive net EV survives."""
    out = []
    for r in rows:
        stop, target = policy.levels(r["close"], r["atr"])
        ev = r["p_target"] * (target - r["close"]) - r["p_stop"] * (r["close"] - stop) - cost
        if ev > 0:
            out.append(Candidate(r["symbol"], r["score"], r["close"], stop, target, ev))
    return sorted(out, key=lambda c: c.net_ev, reverse=True)[:k]


def rotation(vols: dict[str, float], signals: dict[str, float], target_vol: float) -> dict[str, float]:
    """Equal-vol weights for sleeves with positive signal; scaled to target vol; rest to cash."""
    live = {k: v for k, v in vols.items() if signals.get(k, 0) > 0 and v > 0}
    if not live:
        return {"CASH": 1.0}
    inv = {k: 1 / v for k, v in live.items()}
    tot = sum(inv.values())
    w = {k: x / tot for k, x in inv.items()}
    port_vol = sum(w[k] * live[k] for k in w)
    scale = min(1.0, target_vol / port_vol) if port_vol else 0.0
    w = {k: x * scale for k, x in w.items()}
    w["CASH"] = 1 - sum(w.values())
    return w


def simulate_sip(navs: dict[date, float], dates: list[date], amount: float, lag_days: int) -> dict:
    """Each installment buys at the first NAV on or after date+lag (cut-off/funds lag)."""
    days = sorted(navs)
    units = invested = 0.0
    fills: list[tuple[date, date | None]] = []
    for d in dates:
        t = d + timedelta(days=lag_days)
        nxt = next((x for x in days if x >= t), None)
        if nxt is None:
            fills.append((d, None))
            continue
        units += amount / navs[nxt]
        invested += amount
        fills.append((d, nxt))
    value = units * navs[days[-1]]
    return {"units": units, "invested": invested, "value": value, "fills": fills}


def overlap(positions: list[tuple[str, float, tuple[str, ...]]]) -> dict[str, float]:
    """Sum exposure per tag (sector/index/factor); one position may carry several tags."""
    out: dict[str, float] = {}
    for _, value, tags in positions:
        for t in tags:
            out[t] = out.get(t, 0.0) + value
    return out


def exit_guard(risks: dict[str, float], limit: float) -> list[tuple[str, float]]:
    """Fractions to reduce, largest risks first, until the aggregate is within the limit."""
    total = sum(risks.values())
    if total <= limit:
        return []
    excess, cuts = total - limit, []
    for k, r in sorted(risks.items(), key=lambda kv: kv[1], reverse=True):
        if excess <= 0:
            break
        cut = min(r, excess)
        cuts.append((k, cut / r))
        excess -= cut
    return cuts
