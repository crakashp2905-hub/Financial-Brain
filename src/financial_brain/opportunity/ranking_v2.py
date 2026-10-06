"""Ranking v2 (pre-registered in ``PRE-REGISTERED ranking v2``): momentum-led, continuous, no tie-breaking by cost.

    score              mean of the rank-normal scores of 12-1 momentum and nearness to the 52-week high, restandardised
    expected excess    SLOPE[h] x score, the slope of forward excess on score estimated on 2016-2021 only and frozen
    expected net       expected excess minus the itemised costs (decisions/net_alpha)
    order              by score alone; a name is listed only if its expected net is positive

Low volatility and reversal are left out of the long-only ranking: in the half-life table they rank well only through
the bottom of the cross-section, which a long-only book cannot hold.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

SIGNALS = ("mom_12_1", "dist_52w_high")
#: Frozen slopes of forward excess return on the score, estimated on 2016-2021 only (70 monthly dates; "Result of
#: ranking v2"). Never re-fitted on later data: a new estimate is a new version.
SLOPE: dict[int, float] = {20: 0.007269, 60: 0.020438}
VERSION = "ranking-v2"


class RankingError(ValueError):
    pass


def _rank_normal(x: pd.Series) -> pd.Series:
    r = x.rank(pct=True)
    n = r.notna().sum()
    return pd.Series(norm.ppf((r * n - 0.5) / n), index=x.index)


def score(cs: pd.DataFrame) -> pd.Series:
    z = pd.concat([_rank_normal(cs[c]) for c in SIGNALS], axis=1).mean(axis=1, skipna=False)
    sd = z.std()
    if not np.isfinite(sd) or sd == 0:
        raise RankingError("no cross-sectional variation")
    return (z - z.mean()) / sd


def slope(s: pd.Series, fwd: pd.Series) -> float:
    """One date's least-squares slope of forward excess (over the cross-sectional mean) on the score."""
    ok = s.notna() & fwd.notna()
    x, y = s[ok].to_numpy(), fwd[ok].to_numpy()
    y = y - y.mean()
    return float((x * y).sum() / (x * x).sum()) if len(x) > 50 and (x * x).sum() > 0 else float("nan")


def rank(cs: pd.DataFrame, *, horizon: int = 20, capital: float = 1_000_000.0, order_value: float | None = None,
         slopes: dict | None = None) -> pd.DataFrame:
    """The liquid cross-section ordered by score, with expected excess and the itemised net alpha."""
    from ..decisions import net_alpha as NA
    slopes = slopes if slopes is not None else SLOPE
    if horizon not in slopes:
        raise RankingError(f"no frozen slope for h={horizon}; calibrate on 2016-2021 first")
    c = cs[(cs["vol_60"] > 0) & (cs["adv20"] > 0) & (cs["price"] > 0)].dropna(subset=list(SIGNALS)).copy()
    c["score"] = score(c).to_numpy()
    c["expected_return"] = slopes[horizon] * c["score"]
    rows = []
    for r in c.itertuples():
        order = order_value or min(0.05 * capital, 0.10 * r.adv20)
        na = NA.compute(excess=float(r.expected_return), horizon=horizon, order_value=order, adv_inr=float(r.adv20),
                        vol_annual=float(r.vol_60), weight=order / capital)
        rows.append({"cost": na.costs, "expected_net": na.net, "alpha": na.as_dict()})
    c = pd.concat([c.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    return c.sort_values("score", ascending=False).reset_index(drop=True)
