"""The whole market, ranked by what is left after costs.

Every session this answers one question across every liquid name, not only the ones that filed
something: **if I had to pick, which positions have the best net expected return per unit of risk, and
can I actually get them done?** Each name leaves with a disposition and a reason, and all of them -
the taken, the watched and the rejected - are written to opportunity memory so the rejections can be
audited against what the names then did.

## The expected-alpha model, and what it is not

    alpha_h = IC x sigma_h x z                       Grinold (1994), the fundamental law

``z`` is the name's standardised composite score, ``sigma_h`` its own volatility over the horizon, and
``IC`` an **assumed** information coefficient, fixed in advance and deliberately small. It is not
fitted per name and not estimated on the sessions it is applied to. The composite is four signals
chosen from the project's pre-registered, held-out results and combined with equal weights - low
trailing volatility, 12-1 momentum, nearness to the 52-week high, and 20-day reversal - so there is
nothing to tune on the data being scored.

The Grinold form overstates under fat tails, so the number reported is an *estimate to be checked*,
and :func:`replay` exists to check it: it reports realised net excess against the stated expectation.

## Dispositions

    TRADE   positive net alpha, within the top ``top_n`` by net alpha per unit of risk, executable
    WATCH   positive net alpha but ranked below the cut
    REJECT  with a reason: ALPHA (the signal is not positive), COST (the costs eat it), LIQUIDITY (the
            order cannot be done at the participation cap), EVENT (a critical filing is live)

Costs use the sized round trip of :mod:`..costs.india` - statutory and broker charges on the notional
plus square-root impact against the name's own turnover - rather than a flat bucket rate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy.stats import norm

from ..costs.india import CostModel, Segment

SIGNALS = (("vol_60", -1), ("mom_12_1", +1), ("dist_52w_high", +1), ("ret_20d", -1))
HORIZON = 20
ASSUMED_IC = 0.02          #: deliberately small; checked by replay(), never tuned to it
MIN_NET = 0.005            #: below half a percent net over 20 sessions is not worth a position
TOP_N = 20
MAX_WEIGHT = 0.05
KELLY_FRACTION = 0.25
PARTICIPATION_CAP = 0.10   #: an order may be at most this share of a day's turnover
MIN_ORDER_INR = 25_000.0
MIN_ADV = 1e7
GROSS_CAP = 1.0

TRADE, WATCH = "TRADE", "WATCH"
REJECT_ALPHA, REJECT_COST, REJECT_LIQUIDITY, REJECT_EVENT = (
    "REJECT_ALPHA", "REJECT_COST", "REJECT_LIQUIDITY", "REJECT_EVENT")


class EngineError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    ic: float = ASSUMED_IC
    horizon: int = HORIZON
    top_n: int = TOP_N
    max_weight: float = MAX_WEIGHT
    kelly_fraction: float = KELLY_FRACTION
    min_net: float = MIN_NET
    min_adv: float = MIN_ADV
    participation: float = PARTICIPATION_CAP
    portfolio_inr: float = 10_000_000.0


def _gauss_rank(x: pd.Series) -> pd.Series:
    """Rank-normal transform: robust to the fat tails these signals have."""
    r = x.rank(pct=True, na_option="keep")
    n = r.notna().sum()
    return pd.Series(norm.ppf((r * n - 0.5) / n), index=x.index)


def composite(cs: pd.DataFrame) -> pd.Series:
    """Equal-weight mean of the signed rank-normal signals, restandardised to unit variance."""
    zs = [sign * _gauss_rank(cs[c]) for c, sign in SIGNALS]
    z = pd.concat(zs, axis=1).mean(axis=1, skipna=False)
    sd = z.std()
    if not np.isfinite(sd) or sd == 0:
        raise EngineError("the composite has no cross-sectional variation")
    return (z - z.mean()) / sd


def cross_section(con, as_of: date, *, min_adv: float = MIN_ADV) -> pd.DataFrame:
    cols = ", ".join(f"f.{c}" for c, _ in SIGNALS)
    cs = con.execute(f"""
        SELECT f.lineage, p.isin, p.ticker, p.close_adj AS price, f.adv20, {cols}
        FROM features f
        JOIN adjusted_prices p ON p.lineage = f.lineage AND p.business_date = f.business_date
        WHERE f.business_date = ? AND p.close_adj > 0 AND f.adv20 >= ?
    """, [as_of, min_adv]).df()
    return cs.dropna(subset=[c for c, _ in SIGNALS]).reset_index(drop=True)


def live_events(con, as_of: date, lineages: list[str], *, lookback_days: int = 3) -> set[str]:
    """Lineages with a critical filing in the last few days: not a day to open a position blind."""
    rows = con.execute("""
        SELECT DISTINCT l.lineage FROM announcements a
        JOIN security_lineage l ON l.isin = a.isin
        WHERE a.critical AND a.business_date BETWEEN ? AND ?""",
                       [as_of - timedelta(days=lookback_days), as_of]).fetchall()
    return {r[0] for r in rows} & set(lineages)


def decide(cs: pd.DataFrame, *, cfg: Config = Config(), costs: CostModel | None = None,
           blocked: set[str] | None = None) -> pd.DataFrame:
    """Score, cost, rank and dispose of every row of a cross-section."""
    if len(cs) < 50:
        raise EngineError(f"{len(cs)} names is too few to rank a market")
    costs = costs or CostModel()
    blocked = blocked or set()
    out = cs.copy()
    out["z"] = composite(out)
    out["sigma_h"] = out["vol_60"] * np.sqrt(cfg.horizon / 252.0)
    out["alpha"] = cfg.ic * out["sigma_h"] * out["z"]
    nominal = np.minimum(cfg.max_weight * cfg.portfolio_inr, cfg.participation * out["adv20"])
    out["order_inr"] = nominal
    daily_vol = (out["vol_60"] / np.sqrt(252.0)).to_numpy()
    cost = []
    for o, adv, dv in zip(nominal, out["adv20"], daily_vol):
        cost.append(costs.round_trip_sized(order_value=float(o), adv_value=float(adv),
                                           daily_vol=float(dv), segment=Segment.DELIVERY)["total"]
                    if o >= MIN_ORDER_INR else np.nan)
    out["cost"] = cost
    out["net"] = out["alpha"] - out["cost"]
    out["ir"] = out["net"] / out["sigma_h"]

    def reason(r) -> str:
        if r["lineage"] in blocked:
            return REJECT_EVENT
        if not np.isfinite(r["cost"]):
            return REJECT_LIQUIDITY
        if r["alpha"] <= 0:
            return REJECT_ALPHA
        if r["net"] < cfg.min_net:
            return REJECT_COST
        return WATCH

    out["disposition"] = out.apply(reason, axis=1)
    cand = out[out["disposition"] == WATCH].sort_values("ir", ascending=False)
    out.loc[cand.index[:cfg.top_n], "disposition"] = TRADE
    out["rank"] = out["ir"].rank(ascending=False, method="first")
    w = (cfg.kelly_fraction * out["net"] / out["sigma_h"] ** 2).clip(0, cfg.max_weight)
    out["weight"] = np.where(out["disposition"] == TRADE, w, 0.0)
    total = out["weight"].sum()
    if total > GROSS_CAP:
        out["weight"] *= GROSS_CAP / total
    return out.sort_values(["disposition", "ir"], ascending=[True, False]).reset_index(drop=True)


def scan(con, as_of: date, *, cfg: Config = Config(), costs: CostModel | None = None) -> pd.DataFrame:
    cs = cross_section(con, as_of, min_adv=cfg.min_adv)
    return decide(cs, cfg=cfg, costs=costs, blocked=live_events(con, as_of, list(cs["lineage"])))


def to_memory_rows(decisions: pd.DataFrame) -> list[dict]:
    """The engine's decisions in the shape :func:`..opportunity.memory.record` takes."""
    from . import memory as M
    mapping = {TRADE: M.TAKEN, WATCH: M.REJECTED_RANK}
    rows = []
    for r in decisions.itertuples():
        d = r.disposition
        rows.append({"lineage": r.lineage, "rank": int(r.rank) if np.isfinite(r.rank) else None,
                     "score": float(r.z), "disposition": mapping.get(d, M.REJECTED_FILTER),
                     "reason": d if d not in (TRADE, WATCH) else None})
    return rows
