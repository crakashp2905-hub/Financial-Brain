"""Does an edge hold somewhere else? The only test that has beaten this project's overfitting.

Every edge found here has died the same way. A swept rebalance horizon looked worth +246% in sample
and returned -7.49% out of sample. An event filter worth +82.9 points in sample cost 4.53 out of it.
Both were killed by walk-forward, and both had cleared whatever threshold was in front of them first.

Replication across **independent subsamples** is a stronger test than raising a threshold, and the
arithmetic says by how much. Under the null each subsample's t is standard normal, so requiring the
same sign in k of them at |t| >= m has joint probability ``2 * Phi(-m)^k``:

    four tiers, each at |t| >= 2.0, same sign      p = 5.4e-07
    one test at t = 3.72 (Bonferroni over 239)     p = 2.0e-04

The conjunction is **370 times stronger** than the single-test bar, and it asks a different question:
not "is this unlikely under the null" but "is this the same phenomenon in four places". A number fitted
to one subsample has no reason to reappear in another.

## The subsamples

``index_constituents`` is empty in this database - there are levels for about a hundred Nifty and BSE
indexes but no membership - so a tier cannot be "the stocks in Nifty Midcap 150". The tiers are
**point-in-time turnover ranks** instead, which approximate the index families they are named after:

    large   ranks   1- 50     ~ Nifty 50
    next    ranks  51-100     ~ Nifty Next 50
    mid     ranks 101-250     ~ Nifty Midcap 150
    small   ranks 251-500     ~ Nifty Smallcap 250

That is a proxy and it is stated as one. It is also arguably the better instrument for this particular
job: real index membership carries inclusion and exclusion flows, and a signal measured across a
rebalance boundary partly measures index funds trading the boundary rather than the signal.

## Why the tiers are close to independent

The statistic is a **cross-sectional rank correlation computed inside a tier on a single session**.
Because it is cross-sectional and within-session, the market factor is differenced out: a day when
everything rose contributes nothing to any tier's IC. What is left is each tier's own dispersion, and
those are far closer to independent than the tiers' *returns* are.

Not perfectly independent - sector exposure and the size factor still link them - so the joint p is
quoted as what it is: a figure computed under an independence assumption that is approximately, not
exactly, true. The sign-agreement count is reported beside it because that claim needs no assumption
about magnitudes at all.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from statistics import NormalDist

from . import families

_N = NormalDist()

#: (name, first rank, last rank, the index family it approximates).
TIERS = (
    ("large", 1, 50, "~ Nifty 50"),
    ("next", 51, 100, "~ Nifty Next 50"),
    ("mid", 101, 250, "~ Nifty Midcap 150"),
    ("small", 251, 500, "~ Nifty Smallcap 250"),
)

#: The trader's chart toolkit already computed in ``features``, plus the candle patterns. These are
#: hypotheses, not a shortlist: a chart pattern faces the same bar as anything else here.
CHART_FEATURES = (
    "rsi_14", "stoch_k_14", "williams_r_14", "bb_pct_20", "adx_14", "cci_20", "mfi_14",
    "obv_slope_20", "macd_hist", "atr_pctile_250", "dist_52w_high", "mom_12_1",
    "ret_20d", "vol_60", "vr_60",
)

#: Candle patterns live in their own table and are boolean, so they are ranked as 0/1 - which makes the
#: IC a point-biserial correlation rather than a Spearman. Still a valid rank statistic, with far fewer
#: distinct values, so its magnitude is not comparable to a continuous feature's.
CANDLE_FEATURES = ("k_doji", "k_hammer", "k_shooting_star", "k_bullish_engulfing",
                   "k_bearish_engulfing", "k_morning_star", "k_marubozu_bull")

#: Sessions a tier needs before its IC is reported.
MIN_SESSIONS = 100

#: Names a tier needs on a session for that session's IC to count. A rank correlation over five
#: observations takes a handful of values.
MIN_NAMES = 15

#: |t| each tier must reach for a replication to count. Deliberately modest: the strength comes from
#: the conjunction across tiers, not from any single tier clearing a high bar.
TIER_T = 2.0


class ReplicationError(ValueError):
    pass


@dataclass
class TierResult:
    tier: str
    mean_ic: float | None
    t: float | None
    sessions: int
    names_per_session: float
    why: str = ""

    def ok(self) -> bool:
        return self.t is not None


def tier_sql(feature: str, *, candle: bool) -> str:
    """One query returning (tier, session, IC) for every tier and session.

    The ranking, the tiering, the forward return and the rank correlation are all done in SQL in one
    pass. Spearman is computed from ranks taken **within the tier and session**, which is the whole
    point: ranks computed over the full universe would carry the size effect straight into every
    tier's IC.
    """
    src = ("SELECT c.business_date, c.lineage, CAST(c.{f} AS DOUBLE) AS x "
           "FROM candles c").format(f=feature) if candle else (
        "SELECT f.business_date, f.lineage, CAST(f.{f} AS DOUBLE) AS x "
        "FROM features f").format(f=feature)
    cases = "\n".join(
        f"                WHEN rk BETWEEN {lo} AND {hi} THEN '{name}'"
        for name, lo, hi, _ in TIERS)
    return f"""
        WITH ranked AS (
            SELECT f.business_date, f.lineage,
                   ROW_NUMBER() OVER (PARTITION BY f.business_date
                                      ORDER BY f.adv20 DESC, f.lineage) AS rk
            FROM features f
            WHERE f.business_date BETWEEN ? AND ? AND f.adv20 IS NOT NULL
        ), tiered AS (
            SELECT business_date, lineage,
                   CASE
{cases}
                   END AS tier
            FROM ranked
        ), sig AS ({src}),
        fwd AS (
            SELECT lineage, business_date,
                   LEAD(close_adj, ?) OVER (PARTITION BY lineage ORDER BY business_date)
                     / close_adj - 1 AS ret
            FROM adjusted_prices
            WHERE close_adj > 0 AND business_date >= ?
              AND business_date <= CAST(? AS DATE) + INTERVAL 400 DAY
        ), joined AS (
            SELECT t.tier, t.business_date, s.x, w.ret
            FROM tiered t
            JOIN sig s ON s.lineage = t.lineage AND s.business_date = t.business_date
            JOIN fwd w ON w.lineage = t.lineage AND w.business_date = t.business_date
            WHERE t.tier IS NOT NULL AND s.x IS NOT NULL AND w.ret IS NOT NULL
        ), ranks AS (
            -- Ranks within (tier, session). Average ranks for ties, which matters enormously for the
            -- boolean candle features where most names share a value.
            SELECT tier, business_date,
                   RANK() OVER (PARTITION BY tier, business_date ORDER BY x)
                     + (COUNT(*) OVER (PARTITION BY tier, business_date, x) - 1) / 2.0 AS rx,
                   RANK() OVER (PARTITION BY tier, business_date ORDER BY ret)
                     + (COUNT(*) OVER (PARTITION BY tier, business_date, ret) - 1) / 2.0 AS ry
            FROM joined
        )
        SELECT tier, business_date, CORR(rx, ry) AS ic, COUNT(*) AS n
        FROM ranks
        GROUP BY tier, business_date
        HAVING COUNT(*) >= ? AND CORR(rx, ry) IS NOT NULL
        ORDER BY tier, business_date
    """


def by_tier(con, *, feature: str, horizon: int = 20, start: date, end: date,
            candle: bool = False, min_names: int = MIN_NAMES) -> dict:
    """Cross-sectional IC of one feature, measured separately inside each tier."""
    rows = con.execute(tier_sql(feature, candle=candle),
                       [start, end, horizon, start, end, min_names]).fetchall()
    series: dict[str, list[float]] = {}
    counts: dict[str, list[int]] = {}
    for tier, _session, ic, n in rows:
        series.setdefault(tier, []).append(ic)
        counts.setdefault(tier, []).append(n)

    out = []
    for name, _lo, _hi, _approx in TIERS:
        ics = series.get(name, [])
        if len(ics) < MIN_SESSIONS:
            out.append(TierResult(tier=name, mean_ic=None, t=None, sessions=len(ics),
                                  names_per_session=0.0,
                                  why=f"{len(ics)} sessions is below {MIN_SESSIONS}"))
            continue
        k = len(ics)
        m = sum(ics) / k
        sd = math.sqrt(sum((x - m) ** 2 for x in ics) / (k - 1))
        out.append(TierResult(
            tier=name, mean_ic=m,
            t=(m / (sd / math.sqrt(k))) if sd > 0 else None, sessions=k,
            names_per_session=sum(counts[name]) / k))
    return {"feature": feature, "horizon": horizon, "candle": candle,
            "tiers": out, "verdict": verdict(out)}


def verdict(tiers: list[TierResult], *, tier_t: float = TIER_T) -> dict:
    """Did it replicate, and how unlikely is that under the null?

    ``joint_p`` is ``2 * Phi(-min|t|)^k`` over the tiers that reported, which is the probability of
    seeing the same sign everywhere at least this strongly if the feature had no edge. Computed under
    an independence assumption that within-tier cross-sectional ICs make approximately - not exactly -
    true, so the sign-agreement count is given beside it: that claim needs no assumption about
    magnitudes.
    """
    usable = [t for t in tiers if t.ok()]
    if len(usable) < 2:
        return {"replicated": False, "tiers_measured": len(usable),
                "why": "a replication claim needs at least two tiers that could be measured"}
    signs = {1 if t.t > 0 else -1 for t in usable}
    agree = len(signs) == 1
    min_abs = min(abs(t.t) for t in usable)
    k = len(usable)
    joint = 2 * (_N.cdf(-min_abs) ** k)
    return {
        "replicated": bool(agree and min_abs >= tier_t),
        "tiers_measured": k,
        "signs_agree": agree,
        "direction": (1 if next(iter(signs)) > 0 else -1) if agree else 0,
        "min_abs_t": min_abs,
        "max_abs_t": max(abs(t.t) for t in usable),
        "joint_p": joint,
        "tier_t_required": tier_t,
        "why": "replicated means the same sign in every measured tier with |t| >= "
               f"{tier_t} in the WEAKEST of them; joint_p assumes the tiers are independent, "
               "which within-tier cross-sectional ICs make approximately true",
    }


def census(con, *, features=CHART_FEATURES, candles=CANDLE_FEATURES, horizon: int = 20,
           start: date, end: date, progress=None) -> dict:
    """Every feature, tier by tier, with the multiple-testing cost of having asked.

    The bar reported is the single-test Bonferroni bar over the whole ledger including this census.
    It is given for comparison, not as the test: a replication across k tiers is a conjunction and is
    judged by ``joint_p``, which this function Bonferroni-corrects by the number of features tried.
    """
    before = families.census(con)
    results = []
    for f in features:
        r = by_tier(con, feature=f, horizon=horizon, start=start, end=end, candle=False)
        results.append(r)
        if progress:
            progress(r)
    for f in candles:
        r = by_tier(con, feature=f, horizon=horizon, start=start, end=end, candle=True)
        results.append(r)
        if progress:
            progress(r)

    tried = len(features) + len(candles)
    trials_added = sum(r["verdict"].get("tiers_measured", 0) for r in results)
    replicated = [r for r in results if r["verdict"].get("replicated")]
    # Bonferroni on the joint p by the number of features tried - not by the number of tiers, which
    # the conjunction has already paid for.
    survivors = [r for r in replicated
                 if r["verdict"]["joint_p"] * tried < 0.05]
    return {
        "results": sorted(results,
                          key=lambda r: r["verdict"].get("joint_p", 1.0)),
        "features_tried": tried,
        "trials_added": trials_added,
        "single_test_bar_before": before["bar_by_trial"],
        "single_test_bar_after": families.bonferroni(before["trials"] + trials_added),
        "replicated": [(r["feature"], r["verdict"]["direction"],
                        r["verdict"]["min_abs_t"], r["verdict"]["joint_p"])
                       for r in replicated],
        "survive_multiplicity": [(r["feature"], r["verdict"]["joint_p"] * tried)
                                 for r in survivors],
        "horizon": horizon,
        "window": [str(start), str(end)],
        "why": "a feature survives only if it has the same sign in every tier, |t| >= "
               f"{TIER_T} in the weakest, and its joint p times {tried} features is still "
               "below 0.05",
    }
