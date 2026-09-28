"""A forecast is a distribution over paths, and the useful questions are about paths.

A point forecast answers "what will the price be", which is the question nobody actually trades on.
The questions a position is opened on are conditional and path-dependent:

    P(price is above the entry in 20 sessions)
    P(the target is reached before the stop)
    how much is lost in the 5% of futures that go worst

The first can be answered from a marginal distribution. The second cannot - it depends on the
*order* in which prices arrive, and two forecasts with identical 20-session marginals can have very
different first-passage probabilities. So this module keeps the sampled paths and derives everything
from them, rather than storing quantiles and throwing the paths away.

Deliberately pure Python. The core install of this project is duckdb + pytz, and a forecast
distribution is arithmetic over a few thousand floats; a numeric stack belongs behind the optional
extra that the model itself needs, not in front of the object that represents its output.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

#: Quantiles stored by default. The tails are the point of a forecast distribution, so p05/p95 are
#: not optional - but with 20 sampled paths the 5th percentile *is* the worst path, which is an
#: estimate with no interior, so `quantiles()` refuses to report what the sample cannot support.
DEFAULT_QUANTILES = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)

#: Below this many paths, the 10% tails are reported as None rather than as a sample extremum.
#: At n paths the most extreme quantile the sample can speak to is about 1/(n+1); asking for p05
#: from 20 paths is asking for 1-in-20 resolution from a sample whose finest grain is 1-in-21.
MIN_PATHS_FOR_TAILS = 40


class ForecastError(Exception):
    pass


def quantile(xs: list[float], q: float) -> float:
    """Empirical quantile with linear interpolation, on a copy.

    The type-7 definition (the one numpy and R default to), written out because this module has no
    numeric dependency and because the definition matters at the tails, which is where a forecast
    is judged.
    """
    if not xs:
        raise ForecastError("no values")
    if not 0.0 <= q <= 1.0:
        raise ForecastError(f"quantile {q} outside [0, 1]")
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


@dataclass
class ForecastDistribution:
    """Sampled future paths for one instrument, and the questions they can answer.

    ``paths`` is a list of price paths, each of length ``horizon``, each starting one session after
    ``as_of`` and *excluding* ``anchor``. Every path must be the same length: a ragged sample means
    some futures were truncated, and a quantile over a ragged sample silently mixes horizons.
    """

    instrument: str
    as_of: date
    horizon: int
    anchor: float                       #: the last observed price, at ``as_of``
    paths: list[list[float]]
    model: str = ""
    model_version: str = ""
    #: Whatever identifies the data the forecast was conditioned on, so a forecast can be tied back
    #: to a world state rather than to a wall-clock time.
    data_version: str = ""
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.paths:
            raise ForecastError("a forecast distribution with no paths is not a forecast")
        if self.anchor <= 0:
            raise ForecastError(f"anchor {self.anchor} must be positive")
        lengths = {len(p) for p in self.paths}
        if len(lengths) != 1:
            raise ForecastError(
                f"ragged paths {sorted(lengths)}: a quantile over paths of different lengths "
                f"mixes horizons")
        if lengths != {self.horizon}:
            raise ForecastError(
                f"paths are {lengths.pop()} steps but horizon is {self.horizon}")

    # ------------------------------------------------------------------ marginal questions
    @property
    def n_paths(self) -> int:
        return len(self.paths)

    def terminal(self) -> list[float]:
        """The price at the end of the horizon, one per path."""
        return [p[-1] for p in self.paths]

    def terminal_returns(self) -> list[float]:
        return [p[-1] / self.anchor - 1 for p in self.paths]

    def quantiles(self, qs=DEFAULT_QUANTILES) -> dict[str, float | None]:
        """Terminal-price quantiles, with the ones the sample cannot support reported as None.

        A model that returns 20 paths has not measured its own 5th percentile, and reporting the
        minimum path as "p05" is how a forecast acquires a tail it never estimated.
        """
        term = self.terminal()
        out: dict[str, float | None] = {}
        for q in qs:
            name = f"p{int(round(q * 100)):02d}"
            extreme = min(q, 1 - q) < 1 / (self.n_paths + 1)
            thin = self.n_paths < MIN_PATHS_FOR_TAILS and min(q, 1 - q) <= 0.10
            out[name] = None if (extreme or thin) else quantile(term, q)
        return out

    def expected_return(self) -> float:
        rs = self.terminal_returns()
        return sum(rs) / len(rs)

    def expected_volatility(self) -> float:
        """Annualised dispersion *within* a path, averaged over paths - not the spread of terminals.

        These are different numbers and conflating them is a real error: a fan of paths that each
        drift smoothly to a different place has wide terminal spread and low realised volatility,
        and a position with a stop is killed by the second one.
        """
        vols = []
        for p in self.paths:
            prices = [self.anchor] + p
            rets = [prices[i + 1] / prices[i] - 1
                    for i in range(len(prices) - 1) if prices[i] > 0]
            if len(rets) > 1:
                m = sum(rets) / len(rets)
                var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
                vols.append(math.sqrt(var * 250))
        return (sum(vols) / len(vols)) if vols else float("nan")

    def probability_above(self, level: float) -> float:
        """P(terminal price > level). A marginal question, and the weaker of the two kinds."""
        return sum(1 for p in self.paths if p[-1] > level) / self.n_paths

    def probability_below(self, level: float) -> float:
        return sum(1 for p in self.paths if p[-1] < level) / self.n_paths

    # -------------------------------------------------------------- path-dependent questions
    def first_passage(self, *, target: float, stop: float) -> dict:
        """P(target before stop), which is the question a position with a stop actually asks.

        Resolved on closes, because that is what the paths are. A real intraday path can touch a
        stop and close above it, so this **understates** the stop probability - a bias in the
        optimistic direction, which is why it is stated in the return value rather than buried:
        treat ``p_stop_first`` as a floor, not an estimate.

        Where a single session would cross both levels, the stop wins. That case is unresolvable
        from close data, and resolving it in the strategy's favour is exactly the kind of coin-flip
        that turns into an edge in a backtest.
        """
        if target <= stop:
            raise ForecastError(f"target {target} must be above stop {stop}")
        hit_target = hit_stop = neither = 0
        days_to_target: list[int] = []
        for p in self.paths:
            outcome = None
            for i, px in enumerate(p):
                if px <= stop:
                    outcome = "stop"
                    break
                if px >= target:
                    outcome = "target"
                    days_to_target.append(i + 1)
                    break
            if outcome == "target":
                hit_target += 1
            elif outcome == "stop":
                hit_stop += 1
            else:
                neither += 1
        n = self.n_paths
        return {
            "p_target_first": hit_target / n,
            "p_stop_first": hit_stop / n,
            "p_neither": neither / n,
            "median_days_to_target": (quantile([float(d) for d in days_to_target], 0.5)
                                      if days_to_target else None),
            "resolved_on": "closes",
            "caveat": "an intraday path can touch the stop and close above it, so p_stop_first "
                      "is a floor; a session crossing both levels is given to the stop",
        }

    def expected_shortfall(self, alpha: float = 0.05) -> float:
        """Mean terminal return across the worst ``alpha`` of paths.

        The average of the tail, not the edge of it. A VaR quantile says where the tail starts and
        says nothing about how far it goes, and position sizing needs the second number.
        """
        rs = sorted(self.terminal_returns())
        if len(rs) < MIN_PATHS_FOR_TAILS:
            return float("nan")
        k = max(1, int(math.floor(alpha * len(rs))))
        return sum(rs[:k]) / k

    def summary(self, *, target: float | None = None, stop: float | None = None) -> dict:
        out = {
            "instrument": self.instrument,
            "as_of": self.as_of,
            "horizon": self.horizon,
            "anchor": self.anchor,
            "model": self.model,
            "model_version": self.model_version,
            "data_version": self.data_version,
            "n_paths": self.n_paths,
            "expected_return": self.expected_return(),
            "expected_volatility": self.expected_volatility(),
            "expected_shortfall_05": self.expected_shortfall(0.05),
            **self.quantiles(),
        }
        if target is not None and stop is not None:
            out["first_passage"] = self.first_passage(target=target, stop=stop)
        return out
