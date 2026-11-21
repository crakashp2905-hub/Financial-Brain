"""Strategy baselines and checks (FB-0439, FB-0441, FB-0442, FB-0444, FB-0447, FB-0448, FB-0449, FB-0451, FB-0452,
FB-0456, FB-0467, FB-0468, FB-0474).

* ``select_top_fraction`` picks a fraction of a universe by a score. Ties break by name so the choice is repeatable.
* ``gap_fade`` gives a fade signal after an opening gap that is more than 1% and has started to reverse.
* ``orb_signal`` is an opening-range breakout: long above the range high, short below the range low.
* ``vwap_reversion`` fades a move more than ``deviation`` from VWAP, but only when volume is not abnormally high.
* ``rectangle_breakout`` fires only when the breakout is confirmed.
* ``hedge_notional`` sizes an index hedge to a portfolio's beta-weighted exposure (negative means short the index).
* ``covered_call_profit`` gives profit at expiry per share for a covered call.
* ``vertical_limits`` gives max loss and max profit of a bull call debit spread. Labelled PAPER_ONLY.
* ``fx_session_reversion`` fades a close more than ``deviation`` from the session mean.
* ``dividend_reinvest_units`` buys whole units with a cash dividend.
* ``scenario_regression`` lists scenarios whose result sign differs from the expected sign.
* ``bootstrap_paths`` resamples returns with a fixed seed, so the same seed gives the same paths.
* ``generalises`` checks a candidate across several assets: it must be positive on at least a given share of them.
* ``selection_bias_adjust`` lowers a best score by a penalty that grows with the number of trials. Labelled ILLUSTRATIVE.
"""
from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence


def select_top_fraction(scores: Mapping[str, float], fraction: float, ascending: bool) -> list[str]:
    if not (0 < fraction <= 1):
        raise ValueError("fraction must be in (0, 1]")
    ordered = sorted(scores, key=lambda s: ((scores[s] if ascending else -scores[s]), s))
    k = max(1, round(len(ordered) * fraction))
    return ordered[:k]


def gap_fade(prev_close: float, open_: float, close: float) -> str:
    if open_ > prev_close * 1.01 and close < open_:
        return "SHORT"
    if open_ < prev_close * 0.99 and close > open_:
        return "LONG"
    return "NONE"


def orb_signal(close: float, range_high: float, range_low: float) -> int:
    if close > range_high:
        return 1
    if close < range_low:
        return -1
    return 0


def vwap_reversion(close: float, vwap: float, deviation: float, volume_ratio: float, max_volume_ratio: float) -> int:
    if volume_ratio > max_volume_ratio:
        return 0
    if close > vwap * (1 + deviation):
        return -1
    if close < vwap * (1 - deviation):
        return 1
    return 0


def rectangle_breakout(close: float, level: float, confirmed: bool) -> int:
    return 1 if (confirmed and close > level) else 0


def hedge_notional(portfolio_notional: float, beta: float) -> float:
    return -portfolio_notional * beta


def covered_call_profit(spot_now: float, strike: float, premium: float, expiry_spot: float, shares: int) -> float:
    per_share = min(expiry_spot, strike) - spot_now + premium
    return per_share * shares


def vertical_limits(long_strike: float, short_strike: float, debit: float) -> dict[str, float | str]:
    if short_strike <= long_strike or debit <= 0:
        raise ValueError("need short strike above long strike and a positive debit")
    return {"max_loss": debit, "max_profit": (short_strike - long_strike) - debit, "label": "PAPER_ONLY"}


def fx_session_reversion(close: float, session_mean: float, deviation: float) -> int:
    if close > session_mean * (1 + deviation):
        return -1
    if close < session_mean * (1 - deviation):
        return 1
    return 0


def dividend_reinvest_units(units: float, price: float, dividend_per_unit: float) -> int:
    if price <= 0:
        raise ValueError("price must be positive")
    return math.floor(units * dividend_per_unit / price)


def scenario_regression(results: Mapping[str, float], expected_sign: Mapping[str, int]) -> list[str]:
    failing = []
    for name, expected in expected_sign.items():
        if name not in results:
            raise KeyError(f"no result for scenario {name}")
        actual = 1 if results[name] > 0 else -1 if results[name] < 0 else 0
        if actual != expected:
            failing.append(name)
    return sorted(failing)


def bootstrap_paths(returns: Sequence[float], n_paths: int, horizon: int, seed: int) -> list[float]:
    if not returns or n_paths < 1 or horizon < 1:
        raise ValueError("need returns, paths and horizon")
    rng = random.Random(seed)
    out: list[float] = []
    for _ in range(n_paths):
        total = 1.0
        for _ in range(horizon):
            total *= 1.0 + returns[rng.randrange(len(returns))]
        out.append(total - 1.0)
    return out


def selection_bias_adjust(best_score: float, n_trials: int) -> dict[str, float | str]:
    if n_trials < 1:
        raise ValueError("n_trials must be positive")
    return {"adjusted": best_score - 0.1 * math.log(n_trials), "label": "ILLUSTRATIVE"}


def generalises(results_by_asset: Mapping[str, float], min_positive_share: float) -> bool:
    if not results_by_asset:
        raise ValueError("no assets tested")
    if not (0 < min_positive_share <= 1):
        raise ValueError("min_positive_share must be in (0, 1]")
    positive = sum(1 for v in results_by_asset.values() if v > 0)
    return positive / len(results_by_asset) >= min_positive_share
