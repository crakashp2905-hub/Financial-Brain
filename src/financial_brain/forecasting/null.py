"""The forecasters a foundation model has to beat, and the reason most benchmarks are meaningless.

A price forecast is graded against *something*. Choose the wrong something and any model passes:
predicting tomorrow's close as today's close gets a MAPE near 1.5% on Indian equities, which looks
like skill and is arithmetic. Report RMSE against zero and a model that has learned only "prices are
persistent and volatility clusters" scores brilliantly.

So this module provides the nulls, and they are not strawmen:

* :class:`RandomWalk` - drift-free, volatility from the name's own recent history. The honest floor
  for any price forecast, and it is *already* the right answer under a weak-form efficient market.
* :class:`Climatology` - a stationary block bootstrap of the name's own past returns. Carries the
  name's real fat tails, real volatility level and real autocorrelation structure, without any
  information about the present. Beating this is the minimum bar for "the model learned something
  about this situation" as opposed to "the model learned what this stock is like".
* :class:`Drift` - the random walk plus the name's historical mean return, which is the null that
  catches a model being credited for momentum that is just the equity risk premium.

Sampling blocks rather than single days is the point of :class:`Climatology`. An i.i.d. bootstrap
destroys volatility clustering, which makes its paths too smooth, which makes its first-passage
stop probabilities too low - so an i.i.d. null is a null the model beats for free on exactly the
path-dependent questions that matter. Politis & Romano's (1994) stationary bootstrap draws
geometrically distributed block lengths so the resampled series has no periodicity of its own.

Every forecaster here is seeded and reproducible: a null that moves between runs cannot be the thing
a result is measured against.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date

from .distribution import ForecastDistribution, ForecastError

#: Mean block length for the stationary bootstrap, in sessions. Long enough to carry a volatility
#: cluster (Indian equity vol clusters run days to a few weeks), short enough that a 250-session
#: history still supplies many distinct blocks.
MEAN_BLOCK = 10

#: Minimum history a null needs before it will speak. Below this the bootstrap is resampling a
#: handful of returns and its "distribution" is a permutation of a tiny sample.
MIN_HISTORY = 120


def _returns(prices: list[float]) -> list[float]:
    return [prices[i + 1] / prices[i] - 1
            for i in range(len(prices) - 1) if prices[i] > 0]


def _volatility(rets: list[float]) -> float:
    if len(rets) < 2:
        raise ForecastError("need at least two returns for a volatility")
    m = sum(rets) / len(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))


@dataclass
class RandomWalk:
    """Drift-free Gaussian steps at the name's own recent volatility.

    The floor. Under weak-form efficiency this is not a null to be beaten but the correct forecast,
    so a model that fails to beat it has not been shown to be wrong - it has been shown to be
    redundant, which for a 102M-parameter model is the same practical conclusion.
    """

    lookback: int = 250
    seed: int = 0
    name: str = "random-walk"

    def forecast(self, *, instrument: str, as_of: date, prices: list[float],
                 horizon: int, n_paths: int = 1000) -> ForecastDistribution:
        if len(prices) < MIN_HISTORY:
            raise ForecastError(
                f"{len(prices)} prices is below the {MIN_HISTORY} a null needs")
        rets = _returns(prices[-self.lookback:])
        sigma = _volatility(rets)
        rng = random.Random(f"{self.name}|{instrument}|{as_of}|{self.seed}")
        anchor = prices[-1]
        paths = []
        for _ in range(n_paths):
            px, path = anchor, []
            for _ in range(horizon):
                px *= 1 + rng.gauss(0.0, sigma)
                path.append(max(px, 1e-9))
            paths.append(path)
        return ForecastDistribution(
            instrument=instrument, as_of=as_of, horizon=horizon, anchor=anchor,
            paths=paths, model=self.name, model_version="v1",
            meta={"sigma_daily": sigma, "lookback": self.lookback, "seed": self.seed})


@dataclass
class Drift(RandomWalk):
    """The random walk plus the name's own historical mean return.

    Separates two claims that get conflated constantly: "this stock goes up" and "this stock goes up
    *now*". A momentum signal measured against :class:`RandomWalk` is credited for the first; only
    the margin over this null is the second.
    """

    name: str = "drift"

    def forecast(self, *, instrument: str, as_of: date, prices: list[float],
                 horizon: int, n_paths: int = 1000) -> ForecastDistribution:
        if len(prices) < MIN_HISTORY:
            raise ForecastError(
                f"{len(prices)} prices is below the {MIN_HISTORY} a null needs")
        rets = _returns(prices[-self.lookback:])
        mu, sigma = sum(rets) / len(rets), _volatility(rets)
        rng = random.Random(f"{self.name}|{instrument}|{as_of}|{self.seed}")
        anchor = prices[-1]
        paths = []
        for _ in range(n_paths):
            px, path = anchor, []
            for _ in range(horizon):
                px *= 1 + rng.gauss(mu, sigma)
                path.append(max(px, 1e-9))
            paths.append(path)
        return ForecastDistribution(
            instrument=instrument, as_of=as_of, horizon=horizon, anchor=anchor,
            paths=paths, model=self.name, model_version="v1",
            meta={"mu_daily": mu, "sigma_daily": sigma, "seed": self.seed})


@dataclass
class Climatology:
    """Stationary block bootstrap of the name's own past returns (Politis & Romano 1994).

    The null that actually bites. It carries the name's fat tails, its volatility clustering and its
    short-run autocorrelation, and it knows nothing whatsoever about the present - so the margin a
    model wins over it is precisely the part of the model's skill that is conditional. Most of the
    apparent skill in price forecasting is unconditional, and this is the null that says so.

    Block lengths are geometric with mean ``mean_block``, wrapping at the end of the history, which
    is what makes the resampled series stationary: fixed-length blocks give the bootstrap a period
    of its own, and a forecast evaluated against a periodic null inherits the period.
    """

    lookback: int = 750
    mean_block: int = MEAN_BLOCK
    seed: int = 0
    name: str = "climatology"

    def forecast(self, *, instrument: str, as_of: date, prices: list[float],
                 horizon: int, n_paths: int = 1000) -> ForecastDistribution:
        if len(prices) < MIN_HISTORY:
            raise ForecastError(
                f"{len(prices)} prices is below the {MIN_HISTORY} a null needs")
        rets = _returns(prices[-self.lookback:])
        if len(rets) < MIN_HISTORY - 1:
            raise ForecastError(f"{len(rets)} returns is too few to bootstrap")
        rng = random.Random(f"{self.name}|{instrument}|{as_of}|{self.seed}")
        anchor = prices[-1]
        p_new = 1.0 / self.mean_block
        n = len(rets)
        paths = []
        for _ in range(n_paths):
            px, path = anchor, []
            i = rng.randrange(n)
            for step in range(horizon):
                if step > 0 and rng.random() < p_new:
                    i = rng.randrange(n)          # start a new block
                else:
                    i = (i + 1) % n               # continue this one, wrapping
                px *= 1 + rets[i]
                path.append(max(px, 1e-9))
            paths.append(path)
        return ForecastDistribution(
            instrument=instrument, as_of=as_of, horizon=horizon, anchor=anchor,
            paths=paths, model=self.name, model_version="v1",
            meta={"returns_resampled": n, "mean_block": self.mean_block,
                  "seed": self.seed})


#: The nulls a candidate forecaster is reported against. All three, always: a model can beat the
#: random walk by learning the drift and beat the drift by learning the volatility level, and only
#: the margin over climatology is conditional skill.
NULLS = ("random-walk", "drift", "climatology")


def nulls(seed: int = 0) -> dict:
    return {"random-walk": RandomWalk(seed=seed),
            "drift": Drift(seed=seed),
            "climatology": Climatology(seed=seed)}
