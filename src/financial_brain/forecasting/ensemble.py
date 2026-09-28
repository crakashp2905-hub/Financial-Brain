"""Combining forecasters, and the reasons not to average them.

The obvious way to build an ensemble is to average the members' point forecasts. That is wrong twice
over for distributional forecasting.

**Averaging point forecasts throws away the distribution.** Two models that disagree about the
direction produce a mean near zero with *no* spread to show the disagreement, when the honest answer
is a wide distribution. Pooling the *paths* keeps it: the combined distribution is the mixture of the
members' distributions, and disagreement shows up as the dispersion it actually is.

**A mixture is wider than its members, and that is correct.** A linear pool is reliably
under-confident - its intervals are wider than nominal - which is a known and documented property
(Hora 2004; Gneiting & Ranjan 2013), not a bug to be tuned away. Under-confidence is also the safe
direction: an ensemble that overstates its own precision sizes positions too large.

## Weighting

Weights come from out-of-sample CRPS, not from intuition and not from in-sample fit. A member that has
not been scored gets no weight and is *excluded* rather than given a default share: a model with an
unknown skill and a 25% share is an unmeasured bet.

The weighting is deliberately shrunk toward equal. Equal weights are famously hard to beat in
forecast combination (the "forecast combination puzzle"), and CRPS estimated over a few dozen
non-overlapping windows is noisy enough that chasing it produces a weight vector fitted to noise.
:data:`SHRINKAGE` is how much of the way toward the CRPS-implied weights the pool is allowed to go.

## The regime router, and why it is off by default

:class:`RegimeRouter` selects weights per market regime, which is the interesting idea and also five
to ten times the parameters of a single weight vector. With 44 usable sessions in this project's
current study there is not enough data to fit one regime's weights, let alone several, so the router
refuses to route until each regime has :data:`MIN_PER_REGIME` scored observations and falls back to
the pooled weights until then. It reports the fallback rather than hiding it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from . import calibration as C
from .distribution import ForecastDistribution, ForecastError

#: How far the pool may move from equal weights toward the CRPS-implied ones, in [0, 1]. Zero is
#: equal weighting; one trusts the measured CRPS completely. Held well below one because equal
#: weights are hard to beat and a CRPS over a few dozen windows is a noisy estimate.
SHRINKAGE = 0.5

#: Scored observations a regime needs before the router will fit weights for it separately.
MIN_PER_REGIME = 60


@dataclass
class Member:
    """One forecaster in a pool, with whatever is known about its skill."""

    name: str
    forecaster: object
    #: Out-of-sample mean CRPS. None means unmeasured, which means unweighted.
    crps: float | None = None
    n_scored: int = 0

    @property
    def measured(self) -> bool:
        return self.crps is not None and self.crps > 0 and self.n_scored > 0


def weights(members: list[Member], *, shrinkage: float = SHRINKAGE) -> dict[str, float]:
    """Weights from inverse CRPS, shrunk toward equal, over the measured members only.

    Inverse CRPS rather than a softmax over negative CRPS: CRPS is in price units and scales with the
    instrument, so any exponential weighting would need a temperature calibrated per instrument, and
    that temperature is a searched parameter nobody would record. A ratio is scale-free.
    """
    if not 0.0 <= shrinkage <= 1.0:
        raise ForecastError(f"shrinkage {shrinkage} outside [0, 1]")
    usable = [m for m in members if m.measured]
    if not usable:
        raise ForecastError(
            "no member has a measured CRPS. An unmeasured model is not given a default share: "
            "a weight on an unknown skill is an unmeasured bet")
    inv = {m.name: 1.0 / m.crps for m in usable}
    total = sum(inv.values())
    equal = 1.0 / len(usable)
    return {name: (1 - shrinkage) * equal + shrinkage * (v / total)
            for name, v in inv.items()}


def pool(forecasts: dict[str, ForecastDistribution], w: dict[str, float],
         *, n_paths: int = 500, seed: int = 0) -> ForecastDistribution:
    """Mix member distributions into one, by sampling paths in proportion to the weights.

    A *mixture*, not an average. Averaging paths would produce a single smoothed path per index and
    destroy exactly the disagreement the ensemble exists to represent: two members forecasting +10%
    and -10% should give a wide distribution centred near zero, not a confident flat line.

    Members must agree on the instrument, the date, the horizon and the anchor. A pool across
    different anchors is combining forecasts of different quantities, and the result would look
    perfectly reasonable.
    """
    import bisect
    import random

    if not forecasts:
        raise ForecastError("nothing to pool")
    named = {k: v for k, v in forecasts.items() if k in w and w[k] > 0}
    if not named:
        raise ForecastError("no weighted member has a forecast")

    firsts = next(iter(named.values()))
    for name, fc in named.items():
        if (fc.instrument, fc.as_of, fc.horizon) != (
                firsts.instrument, firsts.as_of, firsts.horizon):
            raise ForecastError(
                f"{name} forecasts {fc.instrument} at {fc.as_of}/h{fc.horizon}, not "
                f"{firsts.instrument} at {firsts.as_of}/h{firsts.horizon}")
        if abs(fc.anchor / firsts.anchor - 1) > 1e-9:
            raise ForecastError(
                f"{name} is anchored at {fc.anchor} and {firsts.model} at {firsts.anchor}; "
                f"pooling across anchors combines forecasts of different quantities")

    scale = sum(w[k] for k in named)
    rng = random.Random(f"pool|{firsts.instrument}|{firsts.as_of}|{seed}")
    order = sorted(named)
    cum, acc = [], 0.0
    for k in order:
        acc += w[k] / scale
        cum.append(acc)

    paths: list[list[float]] = []
    for _ in range(n_paths):
        # Inverse-CDF sampling over the weights: pick a member, then one of its paths.
        member = named[order[bisect.bisect_left(cum, rng.random())]]
        paths.append(list(member.paths[rng.randrange(member.n_paths)]))

    return ForecastDistribution(
        instrument=firsts.instrument, as_of=firsts.as_of, horizon=firsts.horizon,
        anchor=firsts.anchor, paths=paths, model="ensemble",
        model_version="+".join(order),
        meta={"weights": {k: w[k] / scale for k in order}, "seed": seed,
              "members": {k: named[k].model for k in order},
              "note": "a linear pool of distributions is wider than its members and reliably "
                      "under-confident (Hora 2004); that is the safe direction"})


@dataclass
class RegimeRouter:
    """Per-regime weights, with a refusal to route when a regime has too few observations.

    The premise is sound and widely reported: momentum works in trends, mean reversion in ranges, and
    a forecaster's edge is regime-dependent. The problem is arithmetic. Routing across R regimes needs
    R weight vectors from the same data that struggled to fit one, and a regime with eight
    observations produces a weight vector fitted to eight numbers. So the router holds the pooled
    weights until a regime has :data:`MIN_PER_REGIME` scored observations of its own, and says which
    it is using.

    Regimes come from ``regime/brain.py``, which is versioned - v1 and v2 disagree on 14.7% of
    sessions - so the version is recorded with the fit. Weights fitted under one regime definition do
    not transfer to another.
    """

    regime_version: str = "v2"
    shrinkage: float = SHRINKAGE
    min_per_regime: int = MIN_PER_REGIME
    #: {regime: {member: mean CRPS}} and {regime: n}, from out-of-sample scoring.
    by_regime: dict[str, dict[str, float]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    pooled: list[Member] = field(default_factory=list)

    def route(self, regime: str | None) -> dict:
        """Weights for a regime, and a statement of whether they are that regime's own."""
        fallback = weights(self.pooled, shrinkage=self.shrinkage)
        n = self.counts.get(regime or "", 0)
        if regime is None:
            return {"weights": fallback, "routed": False, "regime": None, "n": 0,
                    "why": "no regime supplied"}
        if n < self.min_per_regime:
            return {"weights": fallback, "routed": False, "regime": regime, "n": n,
                    "why": f"{n} scored observations in {regime!r} is below "
                           f"{self.min_per_regime}; a weight vector fitted to {n} numbers is "
                           f"fitted to noise"}
        crps = self.by_regime.get(regime, {})
        members = [Member(name=m.name, forecaster=m.forecaster, crps=crps.get(m.name),
                          n_scored=n) for m in self.pooled]
        if not any(m.measured for m in members):
            return {"weights": fallback, "routed": False, "regime": regime, "n": n,
                    "why": f"no member has a CRPS measured inside {regime!r}"}
        return {"weights": weights(members, shrinkage=self.shrinkage), "routed": True,
                "regime": regime, "n": n, "regime_version": self.regime_version}

    def fit(self, records: list[tuple[ForecastDistribution, float, str]]) -> dict:
        """Fit per-regime CRPS from ``(forecast, realised, regime)`` triples.

        Scores each member inside each regime and records the counts, so :meth:`route` can tell a
        regime it has measured from one it has merely seen.
        """
        buckets: dict[str, dict[str, list[float]]] = {}
        for fc, observed, regime in records:
            if fc.n_paths < 2:
                continue
            buckets.setdefault(regime, {}).setdefault(fc.model, []).append(
                C.crps(fc.terminal(), observed))
        self.by_regime = {
            r: {m: sum(v) / len(v) for m, v in per.items() if v}
            for r, per in buckets.items()}
        self.counts = {
            r: min(len(v) for v in per.values()) if per else 0
            for r, per in buckets.items()}
        return {"regimes": sorted(self.by_regime), "counts": dict(self.counts),
                "routable": sorted(r for r, n in self.counts.items()
                                   if n >= self.min_per_regime),
                "regime_version": self.regime_version}


def regime_at(con, session: date, version: str = "v2") -> str | None:
    """The market regime on a session, from the versioned regime table.

    Returns None rather than a default label when the session is not classified. A missing regime
    that reads as "normal" would route every unclassified session into whichever bucket happens to be
    called that.
    """
    row = con.execute("""SELECT regime FROM market_regime
                         WHERE business_date <= ? AND version = ?
                         ORDER BY business_date DESC LIMIT 1""",
                      [session, version]).fetchone()
    return row[0] if row else None
