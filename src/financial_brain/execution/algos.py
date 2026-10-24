"""Execution algorithms, simulated on minute bars. Nothing here places an order.

Pre-registered in ``PRE-REGISTERED the hundred algorithms`` (Group 1). A parent order (a buy of Q shares, from a start
minute to an end minute) is worked by an algorithm that, at each minute, sees only what happened **before** that
minute and returns child orders. The market model is deliberately hard on passive orders:

    market child   fills inside minute t at its typical price x (1 + half-spread + Y sigma_1m sqrt(q / v_t)),
                   at most MAX_PART of that minute's volume (the rest waits)
    limit child    a buy at P fills only if the minute trades through P by a tick (low <= P - tick), and then only
                   PASSIVE_PART of that minute's volume, at P
    completion     whatever is left at the end minute is bought at that minute's close plus spread and impact

Measured: implementation shortfall against the arrival price (the open of the start minute), against the day's VWAP,
the passive share, and the completion share.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

TICK = 0.05
MAX_PART = 0.25
PASSIVE_PART = 0.10
Y_IMPACT = 1.0
HALF_SPREAD_BP = {"mega": 2.0, "large": 4.0, "mid": 8.0}


@dataclass
class Day:
    """One name's minute bars for one session (minutes from midnight), plus what was known before it opened."""
    m: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    curve: np.ndarray            #: expected share of the day's volume in each minute (sums to 1), from past sessions
    sigma_d: float               #: daily volatility from past sessions
    hs: float                    #: half-spread, fraction
    features: dict = field(default_factory=dict)


@dataclass
class Order:
    qty: float
    start: int                   #: index into the day's bars
    end: int


class Algo:
    name = "base"

    def reset(self, day: Day, order: Order, rng: np.random.Generator) -> None:
        self.day, self.order, self.rng = day, order, rng

    def decide(self, t: int, remaining: float, filled: float) -> list[tuple]:
        """Child orders for minute t: ("mkt", qty) or ("lmt", qty, price). Must use bars < t only."""
        raise NotImplementedError

    # helpers available to every algorithm, all strictly before t
    def last(self, t):
        return self.day.c[t - 1] if t > 0 else self.day.o[t]

    def left(self, t):
        return max(self.order.end - t + 1, 1)


def _mkt_price(day: Day, t: int, q: float) -> float:
    tp = (day.h[t] + day.l[t] + day.c[t]) / 3
    v = max(day.v[t], 1.0)
    sigma_1m = day.sigma_d / np.sqrt(375.0)
    return tp * (1 + day.hs + Y_IMPACT * sigma_1m * np.sqrt(q / v))


def simulate(day: Day, order: Order, algo: Algo, *, seed: int = 0,
             force_completion: bool = True) -> dict:
    rng = np.random.default_rng(seed)
    algo.reset(day, order, rng)
    remaining, cost, passive, carry = order.qty, 0.0, 0.0, 0.0  # noqa: F841
    for t in range(order.start, order.end + 1):
        if remaining <= 1e-9:
            break
        for child in algo.decide(t, remaining, order.qty - remaining) or []:
            if remaining <= 1e-9:
                break
            if child[0] == "mkt":
                q = min(child[1], remaining, MAX_PART * max(day.v[t], 0.0))
                if q <= 0:
                    continue
                cost += q * _mkt_price(day, t, q)
                remaining -= q
            elif child[0] == "lmt":
                q, px = min(child[1], remaining), child[2]
                if day.l[t] <= px - TICK and q > 0:
                    f = min(q, PASSIVE_PART * day.v[t])
                    cost += f * px
                    remaining -= f
                    passive += f
            elif child[0] == "sweep":                    # take everything now, impact on the whole size
                q = remaining
                cost += q * _mkt_price(day, t, q)
                remaining = 0.0
    completion = remaining
    if force_completion and remaining > 1e-9:
        t = order.end
        cost += remaining * _mkt_price(day, t, remaining)
        remaining = 0.0
    filled = order.qty - remaining
    avg = cost / filled if filled > 1e-9 else None
    arrival = day.o[order.start]
    vwap = float((day.v * (day.h + day.l + day.c) / 3).sum() / max(day.v.sum(), 1.0))
    return {"is_bp": (avg / arrival - 1) * 1e4 if avg is not None else None,
            "vs_vwap_bp": (avg / vwap - 1) * 1e4 if avg is not None else None,
            "passive": passive / order.qty, "completion": completion / order.qty,
            "avg": avg, "fill_ratio": filled / order.qty,
            "unfilled_ratio": remaining / order.qty}


# ------------------------------------------------------------------------------------------- schedule algorithms

class Immediate(Algo):
    name = "immediate"

    def decide(self, t, remaining, filled):
        return [("mkt", remaining)]


class Sweep(Algo):
    name = "sweep_to_fill"            # #18: everything at once, whatever it costs

    def decide(self, t, remaining, filled):
        return [("sweep",)]


class TWAP(Algo):
    name = "twap"                     # #2

    def decide(self, t, remaining, filled):
        return [("mkt", remaining / self.left(t))]


class TWAPRandom(TWAP):
    name = "twap_random"              # #96 as an equity variant: jittered slices

    def decide(self, t, remaining, filled):
        return [("mkt", remaining / self.left(t) * self.rng.uniform(0.5, 1.5))]


class VWAP(Algo):
    name = "vwap"                     # #1: follow the historical volume curve

    def reset(self, day, order, rng):
        super().reset(day, order, rng)
        w = day.curve[order.start:order.end + 1]
        self.target = np.cumsum(w) / max(w.sum(), 1e-12) * order.qty

    def decide(self, t, remaining, filled):
        return [("mkt", max(self.target[t - self.order.start] - filled, 0.0))]


class TargetVolume(Algo):
    name = "target_volume"            # #8: the curve rescaled by the volume seen so far today

    def decide(self, t, remaining, filled):
        s = self.order.start
        seen = self.day.v[s:t].sum()
        exp_share = self.day.curve[s:t].sum()
        rest_share = self.day.curve[t:self.order.end + 1].sum()
        if t == s or exp_share <= 0:
            return [("mkt", remaining / self.left(t))]
        proj_rest = seen / exp_share * rest_share
        rate = self.day.curve[t] / max(rest_share, 1e-12)
        return [("mkt", remaining * rate if proj_rest > 0 else remaining / self.left(t))]


class POV(Algo):
    def __init__(self, p: float, name: str | None = None):
        self.p, self.name = p, name or f"pov_{int(p * 100)}"      # #3, #7 at 2%

    def decide(self, t, remaining, filled):
        return [("mkt", self.p * (self.day.v[t - 1] if t > 0 else 0.0))]


class TimeVariantPOV(Algo):
    name = "pov_time_variant"         # #9: heavier near the open and the close

    def decide(self, t, remaining, filled):
        mm = self.day.m[t]
        p = 0.15 if (mm < 600 or mm >= 885) else 0.07
        return [("mkt", p * (self.day.v[t - 1] if t > 0 else 0.0))]


class TargetSize(Algo):
    name = "target_size"              # #10: fixed child size at fixed steps

    def reset(self, day, order, rng):
        super().reset(day, order, rng)
        self.child = order.qty / 20
        self.every = max((order.end - order.start) // 20, 1)

    def decide(self, t, remaining, filled):
        return [("mkt", self.child)] if (t - self.order.start) % self.every == 0 else []


class ImplementationShortfall(Algo):
    """#4: Almgren-Chriss trajectory - holdings decay as sinh(kappa (T - t)) / sinh(kappa T): front-loaded."""

    def __init__(self, kappa: float = 0.02, name: str = "is_almgren_chriss"):
        self.kappa, self.name = kappa, name

    def reset(self, day, order, rng):
        super().reset(day, order, rng)
        T = order.end - order.start + 1
        k = self.kappa
        self.hold = np.array([np.sinh(k * (T - i)) / np.sinh(k * T) for i in range(T + 1)]) * order.qty

    def decide(self, t, remaining, filled):
        i = t - self.order.start
        return [("mkt", max(remaining - self.hold[i + 1], 0.0))]


class AdaptiveShortfall(ImplementationShortfall):
    """#66: speeds up when the price runs away from the arrival price, slows when it comes back."""

    def __init__(self):
        super().__init__(0.02, "is_adaptive")

    def decide(self, t, remaining, filled):
        base = super().decide(t, remaining, filled)[0][1]
        drift = self.last(t) / self.day.o[self.order.start] - 1
        mult = 2.0 if drift > 0.001 else 0.5 if drift < -0.001 else 1.0
        return [("mkt", base * mult)]


class MarketOnClose(Algo):
    name = "market_on_close"          # #5: the NSE close is the 15:00-15:30 VWAP

    def decide(self, t, remaining, filled):
        if self.day.m[t] >= 900:
            return [("mkt", remaining / max(self.left(t), 1))]
        return []


class MarketOnOpen(Algo):
    name = "market_on_open"           # #6: all of it in the first minutes

    def decide(self, t, remaining, filled):
        return [("mkt", remaining)] if t - self.order.start < 3 else []


class TimeBlock(Algo):
    def __init__(self, lo: int, hi: int, name: str):
        self.lo, self.hi, self.name = lo, hi, name    # #81: execute only inside a time window

    def decide(self, t, remaining, filled):
        mm = self.day.m[t]
        if self.lo <= mm < self.hi:
            left = max(sum(1 for x in self.day.m[t:self.order.end + 1] if x < self.hi), 1)
            return [("mkt", remaining / left)]
        return []


class VolumeSpikePause(TWAP):
    name = "twap_volume_spike_pause"  # #83: skip a minute after abnormal volume

    def decide(self, t, remaining, filled):
        past = self.day.v[max(self.order.start - 30, 0):t]
        if t > 0 and len(past) > 10 and self.day.v[t - 1] > past.mean() + 3 * past.std():
            return []
        return super().decide(t, remaining, filled)


# ------------------------------------------------------------------------------------------- price-driven algorithms

class Peg(Algo):
    """#21 primary (at the bid), #23 midpoint, #24 offset (ticks below the bid), #27 queue jump (a tick above the bid):
    one resting limit for what remains, re-pegged every minute to the last price."""

    def __init__(self, ref: str, ticks: int = 0, name: str = "peg"):
        self.ref, self.ticks, self.name = ref, ticks, name

    def price(self, t):
        last = self.last(t)
        base = last * (1 - self.day.hs) if self.ref == "bid" else last
        return base - self.ticks * TICK

    def decide(self, t, remaining, filled):
        return [("lmt", remaining, self.price(t))]


class MarketPeg(Algo):
    name = "peg_market"               # #22: pegged to the offer - takes liquidity

    def decide(self, t, remaining, filled):
        return [("mkt", remaining / self.left(t) * 3)]


class Iceberg(Peg):
    name = "iceberg"                  # #11: shows a tenth, reloads from the reserve

    def __init__(self):
        super().__init__("bid", 0, "iceberg")

    def decide(self, t, remaining, filled):
        return [("lmt", min(remaining, self.order.qty * 0.1), self.price(t))]


class ReversePeg(Peg):
    name = "peg_reverse"              # #25: steps back after an adverse run

    def __init__(self):
        super().__init__("bid", 0, "peg_reverse")

    def decide(self, t, remaining, filled):
        if t >= 3 and self.day.c[t - 1] / self.day.c[t - 3] - 1 > 0.0005:
            return []
        return super().decide(t, remaining, filled)


class Discretionary(Peg):
    name = "discretionary"            # #26: rests at the bid, takes liquidity when the price dips below arrival

    def __init__(self):
        super().__init__("bid", 0, "discretionary")

    def decide(self, t, remaining, filled):
        if self.last(t) < self.day.o[self.order.start] * (1 - 0.0005):
            return [("mkt", remaining * 0.25)]
        return super().decide(t, remaining, filled)


class Ladder(Peg):
    name = "ladder"                   # #28: thirds at the bid, one and two ticks below

    def __init__(self):
        super().__init__("bid", 0, "ladder")

    def decide(self, t, remaining, filled):
        p = self.price(t)
        return [("lmt", remaining / 3, p), ("lmt", remaining / 3, p - TICK), ("lmt", remaining / 3, p - 2 * TICK)]


class IOCBursts(Algo):
    name = "ioc_bursts"               # #19: every 5 minutes, take up to a quarter of the last minute's volume

    def decide(self, t, remaining, filled):
        if (t - self.order.start) % 5 == 0 and t > 0:
            return [("mkt", 0.25 * self.day.v[t - 1])]
        return []


class Parametric(Algo):
    """The family the learned optimisers search: front-load ``kappa``, a POV cap, and a passive fraction posted at
    the bid each minute alongside the scheduled market slice."""

    def __init__(self, kappa: float, cap: float, passive: float, name: str = "parametric"):
        self.kappa, self.cap, self.passive, self.name = kappa, cap, passive, name

    def reset(self, day, order, rng):
        super().reset(day, order, rng)
        self.inner = ImplementationShortfall(max(self.kappa, 1e-4))
        self.inner.reset(day, order, rng)

    def decide(self, t, remaining, filled):
        q = self.inner.decide(t, remaining, filled)[0][1]
        cap = self.cap * (self.day.v[t - 1] if t > 0 else 0.0)
        out = [("mkt", min(q * (1 - self.passive), cap) if cap > 0 else q * (1 - self.passive))]
        if self.passive > 0:
            out.append(("lmt", q * self.passive, self.last(t) * (1 - self.day.hs)))
        return out


def library() -> list[Algo]:
    return [Immediate(), Sweep(), TWAP(), TWAPRandom(), VWAP(), TargetVolume(), POV(0.05), POV(0.10),
            POV(0.02, "minimal_impact_pov_2"), TimeVariantPOV(), TargetSize(), ImplementationShortfall(),
            ImplementationShortfall(0.005, "is_patient"), AdaptiveShortfall(), MarketOnOpen(), MarketOnClose(),
            TimeBlock(600, 630, "block_10_00"), TimeBlock(870, 900, "block_14_30"), VolumeSpikePause(),
            Peg("bid", 0, "peg_primary"), Peg("mid", 0, "peg_midpoint"), Peg("bid", 2, "peg_offset_2"),
            Peg("bid", -1, "queue_jump"), MarketPeg(), Iceberg(), ReversePeg(), Discretionary(), Ladder(), IOCBursts()]
