"""The dynamic evidence standard (ADR-0007, accepted 2026-11-16): a graded verdict whose bar moves with what is known.

ADR-0004 asks for |t| >= 2 in each six-year half, which needs an annual Sharpe above ~0.8. Durable equity edges
run 0.3-0.7, so that bar rejects every realistic edge as well as every false one. This module keeps the parts of
ADR-0004 that protect against self-deception (pre-registration, net of costs, a matched random null, forward
confirmation) and replaces the fixed bar with one that depends on three things fixed *before* a series is scored:

    prior class   how plausible the idea was before the data: A (published across markets, economic mechanism,
                  replicated in independent tiers here), B (mechanism or publication, not both), C (found by search)
    family        how many variants were looked at to find it: the p-value is Sidak-adjusted by that count, so
                  every extra look raises the bar for the whole family (this replaces a hard trial cap)
    evidence      full-sample autocorrelation-robust t, required positive in both halves, updated with live
                  sessions as they settle (live evidence carries no multiplicity haircut)

The output is the posterior probability that the edge is real (minimum Bayes factor, Harvey & Liu 2015), a
shrunk Sharpe, a tier, and a capital fraction (quarter-Kelly on the shrunk Sharpe, capped per tier). The tier
thresholds are on the posterior, so the t a strategy needs is not a constant: see :func:`required_t`.

Nothing here arms a broker stage. ``execution/oms/rollout`` still reads ADR-0004/0006 verdicts: letting a dynamic
tier reach stage TICKET is a separate change that needs its own owner decision (ADR-0007).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import norm

from . import standard as S

#: Prior odds *against* the edge being real, by class. Fixed here, before any series is scored under ADR-0007.
PRIOR_ODDS_AGAINST = {"A": 3.0, "B": 9.0, "C": 49.0}
#: Posterior thresholds for each tier, highest first.
TIERS = (("CANDIDATE", 0.90), ("CANDIDATE_SMALL", 0.70), ("INCUBATE", 0.50))
FAIL = "FAIL"
#: Share of risk capital a tier may hold in one strategy, before the Kelly cap.
TIER_CAP = {"CANDIDATE": 0.30, "CANDIDATE_SMALL": 0.10, "INCUBATE": 0.0, FAIL: 0.0}
BACKTEST_HAIRCUT = 0.5          #: a backtest Sharpe is halved before sizing (McLean & Pontiff; Harvey & Liu)
KELLY_FRACTION = 0.25
DEMOTE_LIVE_Z = -1.64           #: live record this far below zero drops a strategy one tier


@dataclass(frozen=True)
class Spec:
    name: str
    prior: str                  #: "A", "B" or "C"
    family: int = 1             #: variants examined to arrive at this one (>= 1)
    direction: int = 1
    note: str = ""


def periods_per_year(index) -> float:
    idx = pd.to_datetime(pd.Index(index)).sort_values()
    if len(idx) < 3:
        return float("nan")
    years = (idx[-1] - idx[0]).days / 365.25
    return float((len(idx) - 1) / years) if years > 0 else float("nan")


def auto_lag(n: int) -> int:
    """Newey-West's rule of thumb."""
    return int(np.floor(4 * (n / 100.0) ** (2.0 / 9.0))) if n > 0 else 0


def sidak_z(z: float, family: int) -> float:
    """The z-score left after correcting a one-sided p-value for ``family`` looks."""
    if not np.isfinite(z):
        return float("nan")
    if z <= 0:
        return float(z)                     # no correction makes a losing result look better or worse
    p = float(norm.sf(z))
    p_adj = 1.0 - (1.0 - p) ** max(int(family), 1)
    p_adj = min(max(p_adj, 1e-300), 1 - 1e-16)
    return float(norm.isf(p_adj))


def posterior(z: float, prior: str) -> float:
    """P(edge is real | z) from the minimum Bayes factor exp(-z^2/2); a non-positive z is no evidence at all."""
    odds = PRIOR_ODDS_AGAINST[prior]
    if not np.isfinite(z):
        return 0.0
    # z <= 0 is evidence against: the Bayes factor in favour of the null grows the same way
    mbf = float(np.exp(-0.5 * z * z)) if z > 0 else float(np.exp(min(0.5 * z * z, 700.0)))
    return float(1.0 / (1.0 + mbf * odds))


def tier_of(p: float) -> str:
    for name, bar in TIERS:
        if p >= bar:
            return name
    return FAIL


def required_t(prior: str, family: int = 1, tier: str = "CANDIDATE_SMALL") -> float:
    """The full-sample t a strategy needs for ``tier``: the bar, as a function of prior and family size."""
    bar = dict(TIERS)[tier]
    mbf = (1.0 / bar - 1.0) / PRIOR_ODDS_AGAINST[prior]
    z_adj = float(np.sqrt(-2.0 * np.log(mbf))) if mbf < 1 else 0.0
    p_adj = float(norm.sf(z_adj))
    p = 1.0 - (1.0 - p_adj) ** (1.0 / max(int(family), 1))
    return float(norm.isf(p))


def combine(z_backtest: float, years_backtest: float, z_live: float | None, years_live: float) -> float:
    """Information-weighted (Stouffer) combination of the haircut backtest z and the live z."""
    if z_live is None or not np.isfinite(z_live) or years_live <= 0:
        return z_backtest
    wb, wl = np.sqrt(max(years_backtest, 0.0)), np.sqrt(years_live)
    return float((wb * z_backtest + wl * z_live) / np.sqrt(wb * wb + wl * wl))


def _demote(tier: str) -> str:
    order = [t for t, _ in TIERS] + [FAIL]
    return order[min(order.index(tier) + 1, len(order) - 1)]


def judge(spec: Spec, net: pd.Series, *, live: pd.Series | None = None, null_pass: bool | None = None,
          lag: int | None = None, split: pd.Timestamp = S.SPLIT) -> dict:
    """Grade one net period series. ``live`` is the per-period live net (excess) record after registration.

    ``null_pass`` is ADR-0004's G4 result where one exists; ``False`` caps the verdict at INCUBATE, ``None``
    (no null run) caps it at CANDIDATE_SMALL - a random-book null is still required for CANDIDATE.
    """
    s = pd.Series(net).dropna() * spec.direction
    s.index = pd.to_datetime(s.index)
    s = s.sort_index()
    n = len(s)
    if n < 24:
        return {"name": spec.name, "tier": FAIL, "why": [f"only {n} periods"], "posterior": 0.0, "n": n}
    ppy = periods_per_year(s.index)
    hac = S.dependence_audit(s, max_lag=auto_lag(n) if lag is None else lag)
    t_full = min(hac["t_hac"], hac["t_iid"]) if hac["t_hac"] > 0 else hac["t_hac"]
    a, b = S.halves(s, split)
    both = bool(len(a) >= 8 and len(b) >= 8 and a.mean() > 0 and b.mean() > 0)
    z_adj = sidak_z(t_full, spec.family)
    years = n / ppy
    z_live, years_live = None, 0.0
    if live is not None and len(pd.Series(live).dropna()) >= 5:
        lv = pd.Series(live).dropna() * spec.direction
        z_live = S.tstat(lv)
        years_live = len(lv) / (periods_per_year(lv.index) if len(lv) > 2 else ppy)
    z = combine(z_adj, years, z_live, years_live)
    p = posterior(z, spec.prior)
    tier, why = tier_of(p), []
    if not both:
        tier, why = FAIL, ["not positive in both halves"]
    if null_pass is False and tier in ("CANDIDATE", "CANDIDATE_SMALL"):
        tier = "INCUBATE"
        why.append("lost to its random-book null")
    if null_pass is None and tier == "CANDIDATE":
        tier = "CANDIDATE_SMALL"
        why.append("no random-book null run")
    if z_live is not None and z_live <= DEMOTE_LIVE_Z and tier != FAIL:
        tier = _demote(tier)
        why.append(f"live record z {z_live:.2f}")
    sharpe = float(s.mean() / s.std(ddof=1) * np.sqrt(ppy)) if s.std(ddof=1) > 0 else float("nan")
    vol = float(s.std(ddof=1) * np.sqrt(ppy))
    sharpe_shrunk = max(sharpe, 0.0) * p * BACKTEST_HAIRCUT
    kelly = KELLY_FRACTION * sharpe_shrunk / vol if vol > 0 else 0.0
    return {"name": spec.name, "prior": spec.prior, "family": spec.family, "n": n, "years": round(years, 1),
            "t_iid": round(hac["t_iid"], 2), "t_hac": round(hac["t_hac"], 2), "t_used": round(float(t_full), 2),
            "z_adj": round(z_adj, 2), "z_live": None if z_live is None else round(float(z_live), 2),
            "t_a": round(S.tstat(a), 2), "t_b": round(S.tstat(b), 2), "both_halves": both,
            "posterior": round(p, 3), "tier": tier, "why": why,
            "need_small": round(required_t(spec.prior, spec.family, "CANDIDATE_SMALL"), 2),
            "need_candidate": round(required_t(spec.prior, spec.family, "CANDIDATE"), 2),
            "sharpe": round(sharpe, 2), "sharpe_shrunk": round(sharpe_shrunk, 2), "vol": round(vol, 3),
            "capital_fraction": round(float(min(kelly, TIER_CAP[tier])), 3)}


def judge_from_halves(spec: Spec, t_a: float, n_a: int, t_b: float, n_b: int, *, null_pass: bool | None = None,
                      ppy: float = 12.0) -> dict:
    """The same grade for an old trial whose series was not kept, from its two half-sample t's (iid)."""
    t_a, t_b = t_a * spec.direction, t_b * spec.direction
    n = n_a + n_b
    t_full = float((np.sqrt(n_a) * t_a + np.sqrt(n_b) * t_b) / np.sqrt(n)) if n else float("nan")
    z_adj = sidak_z(t_full, spec.family)
    p = posterior(z_adj, spec.prior)
    both = bool(t_a > 0 and t_b > 0)
    tier, why = (tier_of(p), []) if both else (FAIL, ["not positive in both halves"])
    if null_pass is False and tier in ("CANDIDATE", "CANDIDATE_SMALL"):
        tier, why = "INCUBATE", ["lost to its random-book null"]
    if null_pass is None and tier == "CANDIDATE":
        tier, why = "CANDIDATE_SMALL", ["no random-book null run"]
    return {"name": spec.name, "prior": spec.prior, "family": spec.family, "n": n, "years": round(n / ppy, 1),
            "t_used": round(t_full, 2), "z_adj": round(z_adj, 2), "t_a": round(t_a, 2), "t_b": round(t_b, 2),
            "both_halves": both, "posterior": round(p, 3), "tier": tier, "why": why,
            "need_small": round(required_t(spec.prior, spec.family, "CANDIDATE_SMALL"), 2),
            "need_candidate": round(required_t(spec.prior, spec.family, "CANDIDATE"), 2)}


def calibrate(*, years: float = 10.5, ppy: float = 12.0, family: int = 1, sharpe: float = 0.0, draws: int = 4000,
              seed: int = 20261116) -> dict:
    """Simulated pass rates: the share of best-of-``family`` series with true annual ``sharpe`` reaching each tier,
    for each prior class. ``sharpe=0`` gives the false-pass rate; ``sharpe=0.5`` the power for a realistic edge."""
    rng = np.random.default_rng(seed)
    n = int(round(years * ppy))
    idx = pd.date_range("2016-01-31", periods=n, freq="ME")
    mu = sharpe / np.sqrt(ppy)
    out = {k: {t: 0 for t, _ in TIERS} for k in PRIOR_ODDS_AGAINST}
    for _ in range(draws):
        x = rng.normal(mu, 1.0, size=(family, n))
        best = x[np.argmax(x.mean(axis=1))]
        s = pd.Series(best, index=idx)
        for k in PRIOR_ODDS_AGAINST:
            r = judge(Spec("sim", k, family), s, null_pass=True)
            order = [t for t, _ in TIERS]
            if r["tier"] != FAIL:
                for t in order[order.index(r["tier"]):]:
                    out[k][t] += 1
    return {k: {t: v / draws for t, v in d.items()} for k, d in out.items()}
