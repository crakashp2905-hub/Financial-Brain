"""Additional 15-minute baselines, intervals and option path outcomes (FB-2411, FB-2413, FB-2417, FB-2418).

* ``persistence_baseline`` / ``vol_tod_baseline``: last-direction and time-of-day volatility baselines.
* ``prediction_interval``: empirical interval of forward returns from the training window.
* ``option_paths``: simulated underlying paths -> option premium outcomes (delta-gamma-theta-vega with
  IV shock and exit at bid), giving a distribution, not a point.
* ``rank_with_tails``: rank candidates by expected net with a 5% tail-loss penalty and marginal
  portfolio exposure.
"""
from __future__ import annotations

import random
import statistics
from collections.abc import Sequence


def persistence_baseline(last_label: str) -> dict[str, float]:
    return {k: (1.0 if k == last_label else 0.0) for k in ("UP", "DOWN", "FLAT")}


def vol_tod_baseline(abs_returns_by_slot: dict[int, Sequence[float]], slot: int, threshold: float
                     ) -> dict[str, float]:
    """P(FLAT) from the share of past same-slot moves within the threshold; the rest split evenly."""
    xs = abs_returns_by_slot.get(slot, ())
    if not xs:
        return {"UP": 1 / 3, "DOWN": 1 / 3, "FLAT": 1 / 3}
    flat = sum(x <= threshold for x in xs) / len(xs)
    return {"UP": (1 - flat) / 2, "DOWN": (1 - flat) / 2, "FLAT": flat}


def prediction_interval(train_returns: Sequence[float], level: float = 0.8) -> tuple[float, float]:
    if len(train_returns) < 10:
        raise ValueError("too few training returns for an interval")
    s = sorted(train_returns)
    lo = s[int((1 - level) / 2 * (len(s) - 1))]
    hi = s[int((1 + level) / 2 * (len(s) - 1))]
    return lo, hi


def option_paths(spot: float, vol_per_step: float, steps: int, *, delta: float, gamma: float,
                 theta_per_step: float, vega: float, iv_shock: float, entry_ask: float,
                 exit_spread: float, n: int = 2000, seed: int = 9) -> dict[str, float]:
    rng = random.Random(seed)
    outs = []
    for _ in range(n):
        s = spot
        for _ in range(steps):
            s *= 1 + rng.gauss(0, vol_per_step)
        m = s - spot
        prem = entry_ask + delta * m + 0.5 * gamma * m * m + theta_per_step * steps + vega * iv_shock
        outs.append(max(prem - exit_spread / 2, 0.0) - entry_ask)
    outs.sort()
    return {"mean": statistics.fmean(outs), "p05": outs[int(0.05 * n)], "p95": outs[int(0.95 * n)],
            "p_loss": sum(o < 0 for o in outs) / n}


def rank_with_tails(cands: dict[str, dict[str, float]], tail_weight: float, exposure_penalty: float
                    ) -> list[tuple[str, float]]:
    """score = mean + tail_weight * p05 - exposure_penalty * marginal_exposure."""
    scored = {k: c["mean"] + tail_weight * c["p05"] - exposure_penalty * c.get("marginal_exposure", 0.0)
              for k, c in cands.items()}
    return sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
