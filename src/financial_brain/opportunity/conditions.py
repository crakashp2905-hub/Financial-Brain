"""The conditions that may be screened for, each with the measurement that earns it a place.

A condition is admissible here only if the trial ledger has tested it. That rule is the whole
design: it makes the registry a *subset* of what has been measured rather than a wish list, and it
means the engine cannot surface something whose historical worth is unknown.

Each entry states:

``expr``      the SQL predicate over ``features``, evaluated point-in-time on one date.
``feature``   the ledger key its record is looked up under, and ``horizon`` the tested horizon.
``claim``     what the condition is supposed to indicate, in one line.
``source``    where the claim comes from - a paper, or this project's own measurement.
``direction`` +1 if the condition is supposed to precede outperformance, -1 underperformance.

Nothing here is a threshold that was tuned. Where a cutoff appears it is either the one the source
paper used, or a round cross-sectional quantile chosen once.

## The honest state of this registry

Of the conditions below, **none has cleared the significance bar**. Two - proximity to the
52-week high and 12-1 momentum - separate from a correctly specified null and fail Bonferroni;
the rest fail both. The registry exists so that what is unusual today can be reported *with*
that record rather than without it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Condition:
    key: str
    expr: str
    feature: str
    horizon: int
    direction: int
    claim: str
    source: str
    #: Set when the condition is known to be a poor instrument for its own claim, with why. The
    #: engine still screens it and shows the caveat rather than hiding a known defect.
    caveat: str | None = None


#: Screened conditions. Cutoffs are cross-sectional and evaluated per date, so "top decile" means
#: relative to that session's universe rather than to a fixed number that drifts with the market.
CONDITIONS: tuple[Condition, ...] = (
    Condition(
        key="near_52w_high",
        expr="dist_52w_high >= -0.02",
        feature="dist_52w_high", horizon=20, direction=1,
        claim="Trading within 2% of its own 52-week high precedes outperformance.",
        source="George & Hwang (2004), The 52-Week High and Momentum Investing",
    ),
    Condition(
        key="momentum_12_1_top_decile",
        expr="mom_12_1 IS NOT NULL",
        feature="mom_12_1", horizon=40, direction=1,
        claim="Top-decile 12-month return skipping the last month precedes outperformance.",
        source="Jegadeesh & Titman (1993); Carhart (1997) fourth factor",
    ),
    Condition(
        key="oversold_rsi",
        expr="rsi_14 <= 30",
        feature="rsi_14", horizon=20, direction=-1,
        claim="RSI below 30 marks a short-term oversold condition that reverts.",
        source="Wilder (1978); tested here as h9",
    ),
    Condition(
        key="short_term_reversal",
        expr="ret_20d IS NOT NULL",
        feature="ret_20d", horizon=20, direction=-1,
        claim="The worst 20-session performers outperform over the following month.",
        source="Jegadeesh (1990); Lehmann (1990)",
    ),
    Condition(
        key="low_volatility",
        expr="vol_60 IS NOT NULL",
        feature="vol_60", horizon=20, direction=-1,
        claim="Lowest-volatility names earn more than their beta justifies.",
        source="Haugen & Heins (1975); Ang et al. (2006); Frazzini & Pedersen (2014)",
        caveat="Measured here against a cap-weighted market the low-volatility quintile has "
               "beta 0.91-0.97 and negative alpha: low residual volatility in Indian equities "
               "does not buy low market beta, which is the mechanism the anomaly needs.",
    ),
    Condition(
        key="above_200dma",
        expr="above_ma200",
        feature="above_ma200", horizon=40, direction=1,
        claim="Trading above the 200-session average is a trend filter.",
        source="Faber (2007); tested here as h11/h12/h13",
        caveat="BOOLEAN, so the quintile machinery that measured it splits a 50/50 tie "
               "arbitrarily. Its reported quintile turnover of 70% was mostly an artifact of "
               "that tie-break; the alpha separation from a correct null is -0.00.",
    ),
    Condition(
        key="high_adx_trend",
        expr="adx_14 >= 25",
        feature="adx_14", horizon=20, direction=1,
        claim="ADX above 25 marks a trending name where trend rules have something to work on.",
        source="Wilder (1978); tested here as h9",
    ),
    Condition(
        key="volatility_expansion",
        expr="atr_pctile_250 >= 0.90",
        feature="atr_14_pct", horizon=20, direction=-1,
        claim="A name in the top decile of its own two-year ATR range is in an unusual state.",
        source="This project's behaviour features; tested as h13",
    ),
    Condition(
        key="mean_reverting_regime",
        expr="vr_60 < 1.0",
        feature="ret_20d", horizon=20, direction=-1,
        claim="Variance ratio below one says the name's moves reverse rather than persist.",
        source="Lo & MacKinlay (1988); used as a conditioner in h13",
    ),
)

BY_KEY = {c.key: c for c in CONDITIONS}

#: Conditions needing a cross-sectional rank rather than an absolute cutoff, and the quantile
#: they take. Kept separate from ``expr`` because a rank has to be computed over the whole
#: session, which is a different query shape from a per-row predicate.
RANKED = {
    "momentum_12_1_top_decile": ("mom_12_1", 0.90, "high"),
    "short_term_reversal": ("ret_20d", 0.10, "low"),
    "low_volatility": ("vol_60", 0.10, "low"),
}
