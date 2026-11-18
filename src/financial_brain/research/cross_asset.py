"""Option scenario P&L and same-exposure routing across wrappers (checklist section 20).

* ``vertical_scenarios`` prices a defined-risk debit vertical under a grid of underlying, implied-vol
  and time shocks with a spread-widening haircut on exit, and an unfilled-second-leg contingency.
* ``route_exposure`` picks, for one desired exposure, the wrapper with the best *implementable* net
  return after spread, fees, tracking and expected exit load. Its clock is labelled: an ETF fills on
  the exchange, a fund allots at a later NAV, so the two are never shown as the same fill.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_call(s: float, k: float, t: float, vol: float, r: float = 0.0) -> float:
    if t <= 0 or vol <= 0:
        return max(s - k, 0.0)
    d1 = (math.log(s / k) + (r + 0.5 * vol * vol) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    return s * _norm_cdf(d1) - k * math.exp(-r * t) * _norm_cdf(d2)


@dataclass(frozen=True)
class Scenario:
    spot_move: float      # fraction
    vol_shift: float      # absolute vol points
    days: float
    pnl: float


def vertical_scenarios(s: float, k_long: float, k_short: float, t_years: float, vol: float,
                       debit: float, lot: int, *, spread_haircut: float,
                       spot_moves: list[float], vol_shifts: list[float], days: list[float]
                       ) -> list[Scenario]:
    """Call debit vertical P&L per lot. Exit value is reduced by ``spread_haircut`` per unit."""
    if not k_long < k_short:
        raise ValueError("call debit vertical needs long strike below short strike")
    out = []
    for m in spot_moves:
        for dv in vol_shifts:
            for d in days:
                t = max(t_years - d / 365, 0.0)
                v = max(vol + dv, 1e-6)
                value = bs_call(s * (1 + m), k_long, t, v) - bs_call(s * (1 + m), k_short, t, v)
                value = max(value - spread_haircut, 0.0)
                out.append(Scenario(m, dv, d, (value - debit) * lot))
    return out


def unfilled_leg_loss(long_leg_cost: float, long_leg_exit_bid: float, lot: int) -> float:
    """If the short leg never fills, the long leg must be exited at its bid: realised contingency."""
    return (long_leg_exit_bid - long_leg_cost) * lot


@dataclass(frozen=True)
class Wrapper:
    name: str
    kind: str                 # ETF | MF | STOCKS
    expected_return: float    # gross, same horizon and exposure
    spread_cost: float
    fees: float
    tracking_cost: float
    exit_load: float
    clock: str                # e.g. "exchange fill" or "NAV allotment T+1"


def route_exposure(options: list[Wrapper]) -> tuple[Wrapper, float]:
    if not options:
        raise ValueError("no wrappers")
    def net(w: Wrapper) -> float:
        return w.expected_return - w.spread_cost - w.fees - w.tracking_cost - w.exit_load
    best = max(options, key=net)
    return best, net(best)
