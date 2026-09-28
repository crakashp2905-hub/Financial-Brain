"""The edge that was missing: a forecast distribution feeding the expected-value arithmetic.

Until now ``forecasting/`` and ``decisions/expected_value.py`` did not know about each other. Nothing
imported the forecasting package at all, so the loop

    forecast -> expected value -> risk -> size -> portfolio gate

was broken at its first arrow. The EV engine took scenarios whose probabilities a human or an LLM had
*stated*, which is the one input in the chain nobody can check.

## Why this is not just reading off p05, p50 and p95

The obvious conversion is to call p05 the bear case, p50 the base case and p95 the bull case, then
attach probabilities to them. Every version of that is wrong, and wrong in a way that survives
inspection:

* **The quantiles are not scenario returns.** p05 is the boundary of the worst 5%, not what happens in
  it. Using the boundary understates the loss - the average outcome *inside* the worst 5% is further
  down, and by exactly the amount that matters for sizing.
* **The probabilities get invented.** Any (0.25, 0.50, 0.25) attached to three quantiles is a number
  from nowhere, and it will not reproduce the distribution's own mean.
* **It silently discards the tails.** Two forecasts with identical p05/p50/p95 and completely different
  behaviour beyond them produce the same scenarios.

So this module **coarsens** the distribution instead of sampling points from it. The terminal returns
are partitioned into contiguous bands; each band becomes a scenario whose probability is the empirical
mass that landed in it and whose return is the *conditional mean* of the outcomes inside it. That
construction has a property the quantile version cannot have:

    sum(p_i * r_i) == the distribution's own expected return, exactly

which is the identity :func:`scenarios_from` is tested on. A coarsening that fails it has either
invented probability or lost some, and either way the EV that follows is not the forecast's EV.

## Where the invalidation level comes from

``expected_value.size`` needs a level at which the thesis is wrong, and its whole design is
*thesis -> invalidation -> size* rather than confidence -> size. A distribution can supply that level
honestly, in two different ways, and they answer different questions:

* a **quantile** stop - the price below which only ``alpha`` of the paths ever end - which is a
  statement about the terminal distribution;
* a **first-passage** stop - a level chosen so that no more than ``alpha`` of paths *touch* it on the
  way - which is the question a real stop order asks, because a stop is hit intraday and never waits
  for the horizon.

The second is always the further-away level, and using the first where the second applies is how a
stop gets hit far more often than its own probability said. :func:`invalidation_from` computes the
first-passage level and says so.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..forecasting.distribution import ForecastDistribution, ForecastError, quantile
from . import expected_value as ev

#: Default band edges, as cumulative probability. Five bands rather than three: a three-band
#: coarsening puts 90% of the mass in one scenario whose conditional mean is close to the overall
#: mean, which throws away the shape the distribution was computed for.
DEFAULT_EDGES = (0.10, 0.30, 0.70, 0.90)

#: Names for the bands the default edges produce, worst first.
DEFAULT_NAMES = ("severe", "bear", "base", "bull", "strong")

#: Paths needed before a coarsening is attempted. Each band must hold enough outcomes for its
#: conditional mean to be an average rather than a single draw; the outermost default band is 10% of
#: the sample, so 100 paths puts ten in it.
MIN_PATHS = 100

#: Largest share of paths allowed to touch the invalidation level. A stop that 20% of futures reach is
#: not an invalidation, it is a coin flip attached to the position.
DEFAULT_STOP_ALPHA = 0.10


@dataclass
class Bridge:
    """A forecast, coarsened into scenarios, with the identity that proves nothing was invented."""

    scenarios: list[dict]
    assessment: ev.Assessment
    expected_return: float          #: the distribution's own mean, for comparison
    mean_error: float               #: EV minus that mean; must be ~0
    n_paths: int
    horizon: int
    model: str
    anchor: float

    def as_dict(self) -> dict:
        return {"scenarios": self.scenarios,
                "expected_value": self.assessment.expected_value,
                "forecast_expected_return": self.expected_return,
                "mean_error": self.mean_error,
                "upside": self.assessment.upside,
                "downside": self.assessment.downside,
                "worst": self.assessment.worst,
                "reward_to_risk": self.assessment.reward_to_risk,
                "n_paths": self.n_paths, "horizon": self.horizon,
                "model": self.model, "anchor": self.anchor,
                "why": "scenario returns are conditional means of contiguous probability bands, so "
                       "the probability-weighted sum reproduces the forecast's own mean"}


def scenarios_from(fc: ForecastDistribution, *, edges=DEFAULT_EDGES,
                   names=DEFAULT_NAMES) -> list[dict]:
    """Coarsen the terminal distribution into contiguous bands, mass-weighted.

    Returns scenarios in :mod:`.expected_value`'s own format, worst band first. The probabilities are
    the empirical mass in each band and the returns are the conditional means inside them, so the
    weighted sum is the distribution's mean by construction rather than by luck.
    """
    if fc.n_paths < MIN_PATHS:
        raise ForecastError(
            f"{fc.n_paths} paths is too few to coarsen: the outermost band would hold "
            f"{int(fc.n_paths * min(edges))} outcomes, and a conditional mean over that many is a "
            f"draw rather than an average")
    if len(names) != len(edges) + 1:
        raise ForecastError(f"{len(edges)} edges make {len(edges) + 1} bands, not {len(names)}")
    if list(edges) != sorted(edges) or not all(0 < e < 1 for e in edges):
        raise ForecastError(f"edges {edges} must be increasing and inside (0, 1)")

    rets = sorted(fc.terminal_returns())
    n = len(rets)
    # Cut on sorted ranks so every path lands in exactly one band and no mass is lost to
    # interpolation. Interpolated quantile boundaries would double-count the paths that straddle one.
    cuts = [0] + [max(1, min(n - 1, round(e * n))) for e in edges] + [n]
    out = []
    for i, name in enumerate(names):
        lo, hi = cuts[i], cuts[i + 1]
        if hi <= lo:
            continue
        block = rets[lo:hi]
        out.append({"name": name,
                    "probability": len(block) / n,
                    "return": sum(block) / len(block),
                    "paths": len(block)})
    total = sum(s["probability"] for s in out)
    if abs(total - 1.0) > 1e-9:
        raise ForecastError(f"bands hold {total:.6f} of the mass, not all of it")
    return out


def invalidation_from(fc: ForecastDistribution, *, alpha: float = DEFAULT_STOP_ALPHA) -> dict:
    """The lowest level no more than ``alpha`` of paths ever *touch*, and the terminal one, together.

    Both are returned because the difference between them is the point. A level chosen from the
    terminal distribution is reached far more often than its quantile says, since a path can dip
    through it and recover before the horizon: the stop is an intraday event and the quantile is not.

    Resolved on closes, which is what the paths are, so the touch probability is a floor - a real
    intraday low can pierce a level the close stayed above. The returned dict says so.
    """
    if not 0 < alpha < 0.5:
        raise ForecastError(f"alpha {alpha} must be in (0, 0.5)")
    terminal = quantile(fc.terminal(), alpha)

    # The touch level: scan candidate levels downward and take the highest whose touch rate is within
    # alpha. Candidates are the per-path minima, which are the only prices at which the touch rate
    # changes.
    minima = sorted(min(p) for p in fc.paths)
    k = int(alpha * len(minima))
    touch = minima[k] if k < len(minima) else minima[-1]

    def touch_rate(level: float) -> float:
        return sum(1 for p in fc.paths if min(p) <= level) / fc.n_paths

    return {
        "first_passage": touch,
        "first_passage_touch_rate": touch_rate(touch),
        "terminal_quantile": terminal,
        "terminal_quantile_touch_rate": touch_rate(terminal),
        "alpha": alpha,
        "anchor": fc.anchor,
        "use": "first_passage",
        "why": "a stop is an intraday event, so it must be placed where few paths TOUCH it, not "
               "where few paths END below it; the terminal quantile is touched far more often than "
               "alpha. Resolved on closes, so both touch rates are floors.",
    }


def bridge(fc: ForecastDistribution, *, edges=DEFAULT_EDGES, names=DEFAULT_NAMES) -> Bridge:
    """Coarsen a forecast and run the EV arithmetic over it.

    Raises through :mod:`.expected_value`'s own checks, deliberately - including its refusal of a
    distribution in which nothing loses money. A forecast whose worst 10% of futures is still positive
    is a forecast that has not named the way it is wrong, and it should fail here rather than be
    waved through because a model produced it.
    """
    scen = scenarios_from(fc, edges=edges, names=names)
    assessment = ev.assess(scen)
    mean = fc.expected_return()
    return Bridge(scenarios=scen, assessment=assessment, expected_return=mean,
                  mean_error=assessment.expected_value - mean, n_paths=fc.n_paths,
                  horizon=fc.horizon, model=fc.model, anchor=fc.anchor)


def proposal(fc: ForecastDistribution, *, portfolio_inr: float,
             alpha: float = DEFAULT_STOP_ALPHA,
             risk_budget: float = ev.DEFAULT_RISK_BUDGET,
             max_weight: float = ev.MAX_WEIGHT,
             edges=DEFAULT_EDGES, names=DEFAULT_NAMES) -> dict:
    """A forecast, all the way to a sized position - or a stated reason it does not become one.

    This is the whole missing arrow in one call: distribution to scenarios to expected value to
    invalidation to size. It returns a proposal, never a decision: the portfolio gate
    (:mod:`..portfolio.gate`) is what turns a weight into an approved weight, and it can still refuse.
    """
    b = bridge(fc, edges=edges, names=names)
    inval = invalidation_from(fc, alpha=alpha)
    out = {"forecast": b.as_dict(), "invalidation": inval}

    if not b.assessment.positive():
        out["proposed_weight"] = 0.0
        out["why_not"] = (f"expected value {b.assessment.expected_value:+.2%} is not positive; "
                          f"there is nothing to size")
        return out

    level = inval["first_passage"]
    if level >= fc.anchor:
        out["proposed_weight"] = 0.0
        out["why_not"] = (f"the {alpha:.0%} first-passage level {level:.2f} is not below the anchor "
                          f"{fc.anchor:.2f}, so there is no distance to be wrong over")
        return out

    sizing = ev.size(portfolio_inr=portfolio_inr, entry=fc.anchor, invalidation=level,
                     risk_budget=risk_budget, max_weight=max_weight)
    out["sizing"] = sizing
    out["proposed_weight"] = sizing["weight"]
    return out
