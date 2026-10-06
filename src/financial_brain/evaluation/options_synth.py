"""Group 5 of the hundred algorithms: options strategies on the Nifty 50, **synthetic**.

There is no options price history in this archive (nightly chain snapshots began 2026-10-05). So options are priced
with Black-Scholes, using India VIX as the implied volatility and the Nifty's actual path to settle them. No skew, no
real quotes, no bid-ask beyond the stated slippage: every number here is an approximation and an upper bound on
realism, labelled synthetic wherever it appears. Nothing here is used by the brain to decide anything.

    short_straddle      #89 / #93  sell the at-the-money call and put each cycle, hold to expiry
    short_strangle      #93        sell 5% out-of-the-money call and put
    iron_condor         #88        short 5% wings, long 8% wings
    hedged_long_straddle #86 / #87 / #52  buy the straddle and delta-hedge daily: earns realised minus implied variance
"""
from __future__ import annotations

import numpy as np
from scipy.stats import norm

R = 0.065                    #: risk-free rate, a year
SLIP = 0.01                  #: 1% of premium per leg per side
STT_SELL = 0.001             #: STT on the sell-side premium


def bs(S, K, T, sigma, call: bool) -> float:
    if T <= 0:
        return max(S - K, 0.0) if call else max(K - S, 0.0)
    d1 = (np.log(S / K) + (R + sigma ** 2 / 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return float(S * norm.cdf(d1) - K * np.exp(-R * T) * norm.cdf(d2)) if call else \
        float(K * np.exp(-R * T) * norm.cdf(-d2) - S * norm.cdf(-d1))


def delta(S, K, T, sigma, call: bool) -> float:
    if T <= 0:
        return float(S > K) if call else -float(S < K)
    d1 = (np.log(S / K) + (R + sigma ** 2 / 2) * T) / (sigma * np.sqrt(T))
    return float(norm.cdf(d1)) if call else float(norm.cdf(d1) - 1)


def _cost(premium_abs: float, sold: bool) -> float:
    return SLIP * premium_abs * 2 + (STT_SELL * premium_abs if sold else 0.0)


def cycles(spot: np.ndarray, vix: np.ndarray, *, days: int = 21, strategy: str = "short_straddle") -> np.ndarray:
    """P&L per cycle, as a fraction of the spot at entry, net of slippage and STT."""
    out = []
    for i in range(0, len(spot) - days, days):
        S0, sig, S1 = spot[i], vix[i] / 100.0, spot[i + days]
        T = days / 252.0
        if strategy in ("short_straddle", "short_strangle", "iron_condor"):
            w = 0.0 if strategy == "short_straddle" else 0.05
            legs = [(S0 * (1 + w), True, -1), (S0 * (1 - w), False, -1)]
            if strategy == "iron_condor":
                legs += [(S0 * 1.08, True, +1), (S0 * 0.92, False, +1)]
            pnl, cost = 0.0, 0.0
            for K, call, q in legs:
                prem = bs(S0, K, T, sig, call)
                payoff = max(S1 - K, 0.0) if call else max(K - S1, 0.0)
                pnl += q * (payoff - prem)
                cost += _cost(prem, sold=(q < 0))
            out.append((pnl - cost) / S0)
        elif strategy == "hedged_long_straddle":
            K = S0
            prem = bs(S0, K, T, sig, True) + bs(S0, K, T, sig, False)
            hedge, cash = 0.0, 0.0
            for j in range(days):
                S = spot[i + j]
                tl = (days - j) / 252.0
                dlt = delta(S, K, tl, sig, True) + delta(S, K, tl, sig, False)
                trade = -dlt - hedge                              # hold minus the straddle's delta in the index
                cash -= trade * S + abs(trade) * S * 0.0003
                hedge += trade
            cash += hedge * S1
            payoff = abs(S1 - K)
            out.append((payoff - prem + cash - _cost(prem, sold=False)) / S0)
    return np.array(out)


def summary(p: np.ndarray, per_year: float = 12.0) -> dict:
    if len(p) < 3:
        return {}
    return {"cycles": int(len(p)), "mean_pct": float(p.mean() * 100), "t": float(p.mean() / (p.std(ddof=1) / np.sqrt(len(p)))),
            "win_rate": float((p > 0).mean()), "worst_pct": float(p.min() * 100),
            "skew": float(((p - p.mean()) ** 3).mean() / p.std() ** 3),
            "annualised_pct": float(p.mean() * per_year * 100)}
