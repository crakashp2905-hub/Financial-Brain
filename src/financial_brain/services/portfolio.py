"""Portfolio and risk reads, and the one write that matters: the gate.

The gate is the only service here that decides anything, and it decides *against* acting far more
often than for it. That asymmetry is deliberate and it is the reason this layer exists: a caller
should not be able to size a position without the book's covariance, its exposures and its stress
history having been consulted, and routing every such request through one function is how that is
enforced rather than remembered.
"""
from __future__ import annotations

from datetime import date

from ..portfolio import gate as pgate
from ..portfolio import signals as psignals
from ..risk import covariance, decompose, exposure, limits, stress
from . import NOT_ESTIMABLE, ServiceError

#: Default book window. With no positions currently open, the windowed form is the one with
#: anything in it, and auditing what the system *did* hold is the useful read.
DEFAULT_WINDOW_DAYS = 400


def book(con, *, as_of: date, window_days: int | None = DEFAULT_WINDOW_DAYS) -> dict:
    """Exposures of the book: groups, liquidity tiers, factor tilts, what is unclassified."""
    return exposure.report(con, as_of, window_days=window_days)


def risk(con, *, as_of: date, window_days: int | None = DEFAULT_WINDOW_DAYS,
         cov_sessions: int = 250) -> dict:
    """Portfolio volatility, risk contributions, effective bets and the covariance's quality.

    The covariance diagnostics are returned alongside the risk numbers, not behind a flag,
    because a portfolio volatility computed from four observations per asset is a property of the
    shrinkage target and a caller has to be able to see that before quoting it.
    """
    held: dict = {}
    for p in exposure.positions(con, as_of, window_days=window_days):
        held[p["isin"]] = held.get(p["isin"], 0.0) + (p["weight"] or 0.0)
    if len(held) < 3:
        raise ServiceError(NOT_ESTIMABLE,
                           f"{len(held)} positions; correlation structure is not meaningful",
                           positions=len(held))
    names = sorted(held)
    try:
        r = covariance.returns(con, names, end=as_of, sessions=cov_sessions)
        est = covariance.shrink_pairwise(r["sparse"], r["isins"])
    except covariance.CovarianceError as exc:
        raise ServiceError(NOT_ESTIMABLE, str(exc)) from exc
    w = [held.get(k, 0.0) for k in est["order"]]
    d = decompose.contributions(est["cov"], w, est["order"])
    pc = decompose.principal_components(est["cov"], est["order"])
    tickers = dict(con.execute("""
        SELECT isin, MAX(ticker) FROM security_listings
        WHERE isin IN (SELECT UNNEST(?)) GROUP BY isin
    """, [est["order"]]).fetchall())
    return {
        "as_of": as_of,
        "portfolio_vol_annual": d["sigma_annual"],
        "effective_bets": d["effective_bets"],
        "effective_bets_entropy": d["effective_bets_entropy"],
        "effective_positions": d["effective_positions"],
        "first_factor_share": d["first_factor_share"],
        "diversification_ratio": d["diversification_ratio"],
        "concentrated": d["concentrated"],
        "positions": [{**p, "ticker": tickers.get(p["name"])} for p in
                      sorted(d["positions"], key=lambda p: -p["risk_share"])],
        "principal_components": [
            {"index": c["index"], "share": c["share"],
             "top_loadings": [(tickers.get(n) or n, round(v, 3))
                              for n, v in c["top_loadings"][:5]]}
            for c in pc["components"][:4]],
        "covariance_quality": {
            "assets": est["assets"], "observations": est["obs"],
            "obs_per_asset": est["obs_per_asset"],
            "shrinkage_delta": est["delta"], "shrinkage_method": est["method"],
            "average_correlation": est["avg_correlation"],
            "under_determined": est["underdetermined"],
            "min_pair_observations": est["min_pair_obs"],
            "intersection_sessions": r["intersection_sessions"],
            "ridge_added": est["ridge_added"],
            "trustworthy": not est["underdetermined"],
        },
    }


def stress_test(con, *, as_of: date, window_days: int | None = DEFAULT_WINDOW_DAYS,
                capital_inr: float | None = None) -> dict:
    """The book's worst historical windows, its conditional volatility and its crisis liquidity."""
    held: dict = {}
    for p in exposure.positions(con, as_of, window_days=window_days):
        held[p["isin"]] = held.get(p["isin"], 0.0) + (p["weight"] or 0.0)
    total = sum(held.values())
    if not total:
        raise ServiceError(NOT_ESTIMABLE, "the book is empty")
    r = stress.report(con, {k: v / total for k, v in held.items()},
                      end=as_of, capital_inr=capital_inr)
    if "note" in r and "windows" not in r:
        raise ServiceError(NOT_ESTIMABLE, r["note"])
    return r


def check_limits(con, *, as_of: date, window_days: int | None = DEFAULT_WINDOW_DAYS) -> dict:
    """Which limits the book currently breaches, and whether each is a limit or a data gap."""
    b = exposure.report(con, as_of, window_days=window_days)
    d = None
    try:
        d = risk(con, as_of=as_of, window_days=window_days)
    except ServiceError:
        d = None
    decomp = None
    if d and d["covariance_quality"]["trustworthy"]:
        decomp = {"sigma_annual": d["portfolio_vol_annual"],
                  "effective_bets": d["effective_bets"],
                  "first_factor_share": d["first_factor_share"],
                  "positions": d["positions"]}
    v = limits.check_book(b, decomp)
    return {
        "as_of": as_of, "verdict": v.verdict,
        "structure_limits_applied": decomp is not None,
        "structure_not_applied_because": (
            None if decomp is not None else
            "the covariance is under-determined, so effective bets and portfolio volatility "
            "would be checked against the shrinkage target rather than a measurement"),
        "breaches": [{"check": x.check, "kind": x.kind, "limit": x.limit,
                      "measured": x.measured, "why": x.why,
                      "max_passing_weight": x.max_passing_weight} for x in v.breaches],
        "data_gaps": [x.check for x in v.data_gaps],
        "measured": v.measured,
    }


def gate(con, *, isin: str, weight: float, as_of: date, capital_inr: float,
         adv_inr: float | None = None,
         window_days: int | None = DEFAULT_WINDOW_DAYS,
         with_stress: bool = True) -> dict:
    """Can the book afford this position, at what size, and why not.

    Returns ALLOW / RESIZE / REFUSE / ABSTAIN with every number behind it. This never places
    anything: the decision path runs through ``decisions/compile.py`` and then the record's state
    machine, and nothing in this project can reach a broker.
    """
    if not 0 < weight <= 1:
        raise ServiceError("bad_request", f"weight {weight} must be in (0, 1]")
    d = pgate.evaluate(con, isin=isin, weight=weight, as_of=as_of,
                       capital_inr=capital_inr, adv_inr=adv_inr,
                       window_days=window_days, with_stress=with_stress)
    return d.as_dict()


def signal_overlap(con, *, a: str, b: str, horizon: int = 20,
                   direction_a: int = 1, direction_b: int = 1) -> dict:
    """Are two signals two bets or one? Holdings overlap and excess-return correlation."""
    return psignals.overlap(con, a, b, horizon,
                            direction_a=direction_a, direction_b=direction_b)
