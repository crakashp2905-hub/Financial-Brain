"""Research families: 150 trials are not 150 independent discoveries, and the bar knows it.

The trial ledger holds 150 entries and the Bonferroni bar is computed over all of them, which
assumes 150 **independent** hypotheses. They are not. Seventy-six of the 150 sit on five features:

    mom_12_1        16 trials
    vol_60          16
    dist_52w_high   15
    above_ma200     15
    ret_20d         14

and those five are about **three** economic ideas. "We tested momentum sixteen ways" is one search
with a budget of sixteen, not sixteen discoveries, and a correction that treats it as the latter is
answering a question nobody asked.

## Why this is not simply an argument for a lower bar

It is the argument a motivated reasoner reaches for, and `dist_52w_high` currently sits at an alpha
t of **+3.12 against a bar of 3.59**. A family-based bar is lower. If that were the end of the
reasoning, this module would be a machine for manufacturing a pass, and it would have been written
immediately after the result that needed one - which it was.

So two things are stated up front and enforced below.

**The correction cuts both ways.** Across families the bar falls, because there are fewer
independent ideas. *Within* a family the representative statistic must be **penalised for its own
search**: the best of sixteen correlated variants is inflated, and reporting the best without that
penalty is the same error in a different place. Done properly with Bonferroni at both levels the two
cancel exactly back to the trial count - the gain comes only from *correlation* between tests inside
a family, and that has to be measured rather than assumed.

**The families were declared after the results existed**, which disqualifies the family bar from
promoting anything already tested. ``DECLARED_AFTER`` records this. A bar chosen with knowledge of
the statistics it will judge is not a bar. It applies to trials recorded from here on.

## What cannot be measured yet, and the concrete fix

The effective number of independent tests inside a family is estimable from the **correlation matrix
of the trials' IC series** - the same eigenvalue-spectrum statistic ``risk/decompose.effective_bets``
already computes for portfolios. `evaluation_runs` stores ``mean_ic`` and ``ic_t`` but **not the
per-rebalance IC series**, so that correlation cannot be computed from the ledger as it stands.

The effective trial count therefore lies somewhere between the family count and the trial count, and
this module reports both bounds and refuses to pick a point inside them. Closing that needs the
firewall to persist its IC series with each run, which is one column and is the highest-value change
to the ledger's schema.
"""
from __future__ import annotations

import math
from datetime import date
from statistics import NormalDist

N = NormalDist()
ALPHA = 0.05

#: The date the taxonomy below was written. Trials recorded before it were run without knowledge of
#: it, so the family bar may describe them but must not promote them - the families were chosen
#: while their results were already visible.
DECLARED_AFTER = date(2026, 9, 27)

#: Features grouped by the economic claim they test, not by their formula. The grouping comes from
#: the literature each signal cites rather than from their measured correlation: a taxonomy fitted
#: to the data would be another search.
#:
#: `momentum` is deliberately broad. Jegadeesh & Titman (1993), George & Hwang (2004) and Faber
#: (2007) are three papers making one claim - that recent relative strength persists - and George &
#: Hwang argue explicitly that the 52-week high *subsumes* momentum. Splitting them to get three
#: families would be exactly the manipulation this module exists to prevent.
FAMILIES: dict[str, tuple[str, ...]] = {
    "momentum": ("mom_12_1", "mom_6_1", "dist_52w_high", "above_ma200", "above_ma50",
                 "ret_250d", "ret_60d", "ma_cross_200", "ma_double_cross_50_200",
                 "faber_taa_10m", "supertrend", "keltner_breakout", "donchian_55_20",
                 "turtle_20_10", "ichimoku_cloud", "pivot_breakout",
                 "above_ma200_band", "above_ma200_atr_band", "trend_only_when_trending",
                 "trend_calm_vix", "adx_14"),
    "reversal": ("ret_20d", "ret_5d", "ret_1d", "rsi_14", "stoch_k_14",
                 "williams_r_14", "bb_pct_20", "cci_20", "bollinger_reversion_20_2"),
    "volatility": ("vol_60", "vol_20", "atr_14_pct", "atr_pct", "atr_pctile_250"),
    "volume": ("obv_slope_20", "mfi_14", "cmf_positive", "awesome_oscillator",
               "macd_hist"),
    "events": ("news_5d", "news_20d", "dealing_60d", "adverse_60d", "days_since_news",
               "timing_score"),
    "candles": tuple(f"candle_{k}_hold20" for k in
                     ("hammer", "shooting_star", "bullish_engulfing", "bearish_engulfing",
                      "morning_star", "marubozu_bull", "doji")),
}

#: Anything not listed is its own family. An unclassified signal is not evidence of independence,
#: but assuming it belongs to an existing family would be a judgement the taxonomy has not earned.
UNASSIGNED = "unassigned"


def family_of(feature: str) -> str:
    for name, members in FAMILIES.items():
        if feature in members:
            return name
        # Event strategies are registered as `event_<TYPE>` in the time-series harness.
        if name == "events" and feature.startswith("event_"):
            return name
        if name == "candles" and feature.startswith("candle_"):
            return name
    return UNASSIGNED


def bonferroni(trials: int, alpha: float = ALPHA) -> float:
    return N.inv_cdf(1 - alpha / (2 * max(trials, 1)))


def search_penalty(variants: int) -> float:
    """How much the best of ``variants`` correlated tries is inflated, in t units.

    ``sqrt(2 ln m)``, the expected maximum of m independent standard normals. It is an **upper**
    bound on the inflation here, because variants within a family are positively correlated and the
    maximum of correlated draws is smaller than that of independent ones. Reported as an upper bound
    rather than a point estimate for exactly that reason.

    The same arithmetic priced the per-stock selection bias in `evaluation/genome.py`, where it
    predicted 2.35 t units against 8.9 measured - because that selector re-optimised at 120 points
    and the bias compounded. A single best-of-m does not compound, so here the bound is meaningful.
    """
    return math.sqrt(2 * math.log(variants)) if variants > 1 else 0.0


def census(con) -> dict:
    """Trials by family, with both bars and the search budget each family has spent."""
    rows = con.execute("""
        SELECT feature, COUNT(*), MAX(ABS(ic_t)), MAX(sharpe),
               MIN(CAST(run_at AS DATE)), MAX(CAST(run_at AS DATE))
        FROM evaluation_runs GROUP BY feature
    """).fetchall()
    fam: dict[str, dict] = {}
    for feature, n, ict, sh, first, last in rows:
        f = family_of(feature)
        d = fam.setdefault(f, {"family": f, "trials": 0, "features": [],
                               "best_abs_ic_t": None, "best_sharpe": None,
                               "first": first, "last": last})
        d["trials"] += n
        d["features"].append({"feature": feature, "trials": n, "best_abs_ic_t": ict})
        if ict is not None and (d["best_abs_ic_t"] is None or ict > d["best_abs_ic_t"]):
            d["best_abs_ic_t"] = ict
        if sh is not None and (d["best_sharpe"] is None or sh > d["best_sharpe"]):
            d["best_sharpe"] = sh
        d["first"] = min(d["first"], first) if d["first"] and first else (first or d["first"])
        d["last"] = max(d["last"], last) if d["last"] and last else (last or d["last"])

    total_trials = sum(d["trials"] for d in fam.values())
    for d in fam.values():
        d["features"].sort(key=lambda x: -x["trials"])
        d["search_penalty_upper"] = search_penalty(d["trials"])
        if d["best_abs_ic_t"] is not None:
            # The family's statistic, penalised for the search that produced it. An upper-bound
            # penalty makes this a *lower* bound on the family's true evidence.
            d["search_adjusted_ic_t"] = d["best_abs_ic_t"] - d["search_penalty_upper"]

    families = len(fam)
    return {
        "trials": total_trials,
        "families": families,
        "bar_by_trial": bonferroni(total_trials + 1),
        "bar_by_family": bonferroni(families + 1),
        # The honest interval. The effective number of independent tests is somewhere between the
        # number of families and the number of trials, and the ledger cannot say where.
        "effective_trials_lower": families,
        "effective_trials_upper": total_trials,
        "bar_interval": (bonferroni(total_trials + 1), bonferroni(families + 1)),
        "declared_after": DECLARED_AFTER,
        "by_family": sorted(fam.values(), key=lambda d: -d["trials"]),
        "why_the_interval_cannot_be_narrowed":
            "the effective count needs the correlation between trials' IC series, and "
            "evaluation_runs stores mean_ic and ic_t but not the per-rebalance series",
    }


def effective_tests(con, *, family: str | None = None,
                    min_overlap: int = 12) -> dict:
    """The effective number of **independent** tests, measured from the trials' IC series.

    Two trials on the same idea have highly correlated IC series; the eigenvalue spectrum of their
    correlation matrix says how many independent directions those trials really explored. It is the
    same statistic ``risk/decompose.effective_bets`` computes for a portfolio, for the same reason:
    counting positions overstates diversification exactly as counting trials overstates independent
    evidence.

    Returns ``None`` for the estimate when too few trials carry a series. That is the state of the
    ledger as written - 150 trials recorded before ``ic_series`` existed - so this closes the
    ``[families, trials]`` interval only as new trials accumulate, and says so rather than
    interpolating.
    """
    from ..risk import decompose, linalg

    rows = con.execute("""
        SELECT feature, horizon, ic_series, ic_dates FROM evaluation_runs
        WHERE ic_series IS NOT NULL AND LEN(ic_series) >= ?
    """, [min_overlap]).fetchall()
    if family:
        rows = [r for r in rows if family_of(r[0]) == family]
    if len(rows) < 3:
        return {"family": family, "trials_with_series": len(rows),
                "effective_tests": None,
                "why": f"{len(rows)} trials carry an IC series; at least 3 are needed. The ledger's "
                       f"first 150 trials were recorded before the series was stored, so the "
                       f"effective count stays bounded rather than estimated."}

    # Align on shared rebalance dates: two trials at different horizons rebalance on different
    # days, and correlating unaligned series would compare different weeks.
    series = []
    for feature, horizon, ics, dates in rows:
        series.append(({d: v for d, v in zip(dates, ics) if v is not None},
                       f"{feature}@{horizon}"))
    common = sorted(set.intersection(*(set(m) for m, _ in series)))
    if len(common) < min_overlap:
        return {"family": family, "trials_with_series": len(rows),
                "effective_tests": None,
                "why": f"only {len(common)} rebalance dates are shared by all "
                       f"{len(rows)} trials; {min_overlap} are needed to correlate them"}

    names = [n for _, n in series]
    cols = [[m[d] for d in common] for m, _ in series]
    n = len(cols)
    import statistics as st
    means = [st.fmean(c) for c in cols]
    sds = [st.pstdev(c) for c in cols]
    corr = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if sds[i] == 0 or sds[j] == 0:
                corr[i][j] = 1.0 if i == j else 0.0
                continue
            corr[i][j] = sum((cols[i][t] - means[i]) * (cols[j][t] - means[j])
                             for t in range(len(common))) / (
                len(common) * sds[i] * sds[j])
    if not linalg.is_pd(corr):
        corr = linalg.nearest_pd(corr)
    enb = decompose.effective_bets(corr)
    return {
        "family": family, "trials_with_series": n, "shared_dates": len(common),
        "effective_tests": enb["herfindahl"],
        "effective_tests_entropy": enb["entropy"],
        "first_factor_share": enb["first_share"],
        "trials": names,
        "bar_at_effective": bonferroni(max(1, round(enb["herfindahl"])) + 1),
        "note": "effective tests from the eigenvalue spectrum of the trials' IC correlation "
                "matrix. Well below the trial count means the trials explored one idea many ways.",
    }


def verdict(con, *, feature: str, statistic: float, run_on: date | None = None,
            statistic_is_net: bool = True) -> dict:
    """Judge one result against both bars, and say plainly which one is allowed to apply.

    ``statistic`` should be the **net or alpha** t, not the IC t. The two disagree routinely here -
    `vol_60` holds the largest IC on record at +12.30 with negative alpha - and a gate that takes
    whichever is larger is not a gate.
    """
    c = census(con)
    fam = family_of(feature)
    row = next((d for d in c["by_family"] if d["family"] == fam), None)
    variants = row["trials"] if row else 1
    penalty = search_penalty(variants)
    ran = run_on or date.today()
    eligible = ran > DECLARED_AFTER

    return {
        "feature": feature, "family": fam,
        "statistic": statistic, "statistic_is_net": statistic_is_net,
        "family_variants_tried": variants,
        "search_penalty_upper": penalty,
        "statistic_after_search_penalty": statistic - penalty,
        "bar_by_trial": c["bar_by_trial"],
        "bar_by_family": c["bar_by_family"],
        "clears_trial_bar": statistic > c["bar_by_trial"],
        # The family bar applied to the *penalised* statistic. Both halves or neither: taking the
        # lower bar without the penalty is the manipulation, not the correction.
        "clears_family_bar_after_penalty":
            (statistic - penalty) > c["bar_by_family"],
        "family_bar_eligible": eligible,
        "verdict": (
            "PASS" if statistic > c["bar_by_trial"]
            else "PASS_ON_FAMILY_BAR" if (eligible and (statistic - penalty) > c["bar_by_family"])
            else "REJECT"),
        "why": (
            "clears the trial-count bar, which needs no argument about families"
            if statistic > c["bar_by_trial"] else
            f"does not clear the trial bar of {c['bar_by_trial']:.2f}; "
            + (f"the family bar of {c['bar_by_family']:.2f} applies to the search-penalised "
               f"statistic {statistic - penalty:+.2f} after {variants} variants, and "
               + ("that clears" if (statistic - penalty) > c["bar_by_family"] else "that does not")
               if eligible else
               f"the family bar cannot apply: this was run on {ran}, before the taxonomy was "
               f"declared on {DECLARED_AFTER}, so the families were chosen with its result "
               f"already visible")),
    }
