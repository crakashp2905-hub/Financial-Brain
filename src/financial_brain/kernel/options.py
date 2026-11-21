"""Option reference pricing, Greeks, implied volatility and payoff (FB-0293, FB-0295..0296, FB-0298..0299, FB-0306).

Black-Scholes-Merton for European options with a continuous dividend yield. Inputs are validated; expiry and
volatility must be positive, and an expired option has intrinsic value only. Greeks use the declared units:
delta per unit of underlying, vega and theta per 1.00 change in volatility and per year respectively.
"""
from __future__ import annotations

import math


class OptionError(ValueError):
    pass


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _check(spot: float, strike: float, t: float, vol: float) -> None:
    if spot <= 0 or strike <= 0:
        raise OptionError("spot and strike must be positive")
    if t < 0 or vol < 0 or not all(math.isfinite(v) for v in (spot, strike, t, vol)):
        raise OptionError("time and volatility must be finite and non-negative")


def bsm_price(spot: float, strike: float, t: float, rate: float, vol: float, call: bool, div: float = 0.0) -> float:
    _check(spot, strike, t, vol)
    if t == 0 or vol == 0:
        fwd = spot * math.exp(-div * t) - strike * math.exp(-rate * t)
        return max(fwd, 0.0) if call else max(-fwd, 0.0)
    d1 = (math.log(spot / strike) + (rate - div + 0.5 * vol * vol) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    if call:
        return spot * math.exp(-div * t) * _norm_cdf(d1) - strike * math.exp(-rate * t) * _norm_cdf(d2)
    return strike * math.exp(-rate * t) * _norm_cdf(-d2) - spot * math.exp(-div * t) * _norm_cdf(-d1)


def greeks(spot: float, strike: float, t: float, rate: float, vol: float, call: bool, div: float = 0.0) -> dict:
    _check(spot, strike, t, vol)
    if t <= 0 or vol <= 0:
        raise OptionError("Greeks need positive time and volatility")
    sq = math.sqrt(t)
    d1 = (math.log(spot / strike) + (rate - div + 0.5 * vol * vol) * t) / (vol * sq)
    d2 = d1 - vol * sq
    dis, dvd = math.exp(-div * t), math.exp(-rate * t)
    delta = dis * _norm_cdf(d1) if call else dis * (_norm_cdf(d1) - 1.0)
    gamma = dis * _norm_pdf(d1) / (spot * vol * sq)
    vega = spot * dis * _norm_pdf(d1) * sq
    common = -spot * dis * _norm_pdf(d1) * vol / (2 * sq)
    if call:
        theta = common - rate * strike * dvd * _norm_cdf(d2) + div * spot * dis * _norm_cdf(d1)
    else:
        theta = common + rate * strike * dvd * _norm_cdf(-d2) - div * spot * dis * _norm_cdf(-d1)
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


def implied_vol(price: float, spot: float, strike: float, t: float, rate: float, call: bool,
                div: float = 0.0, lo: float = 1e-4, hi: float = 5.0, tol: float = 1e-8) -> float:
    """Bisection on volatility, bounded. Prices outside the no-arbitrage band raise instead of guessing."""
    if t <= 0:
        raise OptionError("implied vol needs positive time")
    intrinsic = max(spot * math.exp(-div * t) - strike * math.exp(-rate * t), 0.0) if call else \
        max(strike * math.exp(-rate * t) - spot * math.exp(-div * t), 0.0)
    upper = spot * math.exp(-div * t) if call else strike * math.exp(-rate * t)
    if not (intrinsic - 1e-12 <= price <= upper + 1e-12):
        raise OptionError("price violates no-arbitrage bounds; no implied volatility")
    a, b = lo, hi
    for _ in range(200):
        mid = 0.5 * (a + b)
        if bsm_price(spot, strike, t, rate, mid, call, div) > price:
            b = mid
        else:
            a = mid
        if b - a < tol:
            break
    vol = 0.5 * (a + b)
    if abs(bsm_price(spot, strike, t, rate, vol, call, div) - price) > 1e-6:
        raise OptionError("implied volatility did not converge")
    return vol


def put_call_parity_gap(call_px: float, put_px: float, spot: float, strike: float, t: float, rate: float,
                        div: float = 0.0) -> float:
    """C - P - (S e^{-qT} - K e^{-rT}). Zero for consistent European prices; a nonzero value is an alert, not a trade."""
    return call_px - put_px - (spot * math.exp(-div * t) - strike * math.exp(-rate * t))


def payoff_at_expiry(legs: list[dict], spot_grid: list[float]) -> list[float]:
    """Net expiry payoff of legs: {'call': bool, 'strike': x, 'qty': n (negative = short), 'premium': p}."""
    out = []
    for s in spot_grid:
        total = 0.0
        for leg in legs:
            intrinsic = max(s - leg["strike"], 0.0) if leg["call"] else max(leg["strike"] - s, 0.0)
            total += leg["qty"] * (intrinsic - leg["premium"])
        out.append(total)
    return out
