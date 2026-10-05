"""Scanners over the whole liquid market, each emitting one standard ``Opportunity`` - with net alpha at the centre.

Every scanner asks one question of every liquid name and, where the answer is yes, emits the same record:

    instrument, ticker, as_of, horizon, type, features, evidence, expected_return, expected_risk, liquidity,
    cost, expected_net, evidence_t, expiry, invalidation

**Expected return is never invented.** It comes from the scanner's *measured* information coefficient at its
measured horizon (Grinold: IC x sigma_h x z), shrunk by 0.71, the calibration slope the engine replay found
between stated and realised alpha. A scanner whose edge has not been demonstrated - or was tested and failed -
still emits its opportunities, with an expected return of **zero** and its evidence status, so the record of what
it flagged accumulates and can later be scored. Nothing gets a number it has not earned.

    expected_net = expected_return - sized round-trip cost

There is no "confidence: 82%": the evidence is stated as its status, its source and the t-statistic of the study
behind it.

| scanner             | condition                                   | horizon | evidence                                |
|---------------------|---------------------------------------------|---------|-----------------------------------------|
| momentum            | top 20% by 12-1 month return                | 60      | replicated + held out (in time: in-sample) |
| relative_strength   | top 20% by nearness to the 52-week high     | 60      | replicated + held out                   |
| low_volatility      | bottom 20% by 60-day volatility             | 60      | replicated + held out                   |
| composite           | the engine's four-signal composite, top 20% | 20      | IC 0.083 (t 5.5), top-20 book net negative |
| mean_reversion      | bottom 10% by 20-day return                 | 5       | measured, but cost-dominated: 0         |
| breakout            | within 1% of the 52w high, ADX > 25         | 10      | swing study: none                       |
| volume_anomaly      | today's turnover >= 3x its 20-day average   | 5       | untested: 0                             |
| volatility_expansion| ATR in the top 10% of its own year          | 20      | the low-vol result implies negative     |
| event               | a material filing in the last day           | 5       | event-type analogue edge (small)        |
| fundamental_value   | top 20% by earnings yield (when available)  | 60      | faint, primary sample only: 0           |
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..costs.india import CostModel
from ..decisions import plan as P

SHRINK = 0.71           #: realised/stated alpha slope measured in the engine replay

#: Measured IC by scanner at its horizon (positive = the condition predicts outperformance), the t of that
#: measurement, the evidence status and the source note. None means no demonstrated edge: expected return 0.
EVIDENCE = {
    "momentum": (0.068, 5.9, "REPLICATED_HELD_OUT", "Result of half-life and second-order spillover (h=60)"),
    "relative_strength": (0.094, 6.7, "REPLICATED_HELD_OUT", "Result of half-life and second-order spillover (h=60)"),
    "low_volatility": (0.086, 5.6, "REPLICATED_HELD_OUT", "Result of half-life and second-order spillover (h=60)"),
    "composite": (0.082, 5.4, "IN_SAMPLE", "Result of the opportunity engine replay (h=20)"),
    "mean_reversion": (None, 3.1, "COST_DOMINATED", "Result of swing setups; Trader layers (reversal sleeve -0.84%/mo net)"),
    "breakout": (None, 1.5, "NONE", "Result of swing setups"),
    "volume_anomaly": (None, None, "UNTESTED", "-"),
    "volatility_expansion": (-0.086, 5.6, "REPLICATED_HELD_OUT_INVERSE", "the low-volatility result read from the other end"),
    "event": (None, None, "ANALOGUE_ONLY", "Analogues report edge over the all-filings baseline"),
    "fundamental_value": (None, 1.5, "FAINT_PRIMARY_ONLY", "Result of fundamentals factors (primary sample)"),
}
HORIZON = {"momentum": 60, "relative_strength": 60, "low_volatility": 60, "composite": 20, "mean_reversion": 5,
           "breakout": 10, "volume_anomaly": 5, "volatility_expansion": 20, "event": 5, "fundamental_value": 60}


@dataclass
class Opportunity:
    instrument: str
    ticker: str
    as_of: date
    horizon: int
    type: str
    expected_return: float
    expected_risk: float
    liquidity: float
    cost: float
    expected_net: float
    evidence: str
    evidence_t: float | None
    source: str
    expiry: date
    invalidation: float
    price: float
    features: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["features"] = {k: (None if v is None or (isinstance(v, float) and not np.isfinite(v)) else v)
                         for k, v in self.features.items()}
        return d


def _z(x: pd.Series) -> pd.Series:
    r = x.rank(pct=True)
    from scipy.stats import norm
    n = r.notna().sum()
    return pd.Series(norm.ppf((r * n - 0.5) / n), index=x.index)


def _expiry(d: date, sessions: int) -> date:
    return date.fromordinal(d.toordinal() + int(sessions * 365 / 252) + 1)


def cross_section(con, as_of: date, min_adv: float = 1e7) -> pd.DataFrame:
    cs = con.execute("""
        SELECT f.lineage, p.isin, p.ticker, p.close_adj AS price, p.turnover, f.adv20, f.vol_60, f.mom_12_1,
               f.dist_52w_high, f.ret_20d, f.ret_1d, f.adx_14, f.atr_pctile_250, f.atr_14_pct
        FROM features f JOIN adjusted_prices p ON p.lineage = f.lineage AND p.business_date = f.business_date
        WHERE f.business_date = ? AND p.close_adj > 0 AND f.adv20 >= ?""", [as_of, min_adv]).df()
    return cs.dropna(subset=["vol_60", "mom_12_1", "dist_52w_high", "ret_20d"]).reset_index(drop=True)


def conditions(cs: pd.DataFrame, events: set | None = None, ep: dict | None = None) -> dict[str, pd.Series]:
    """Which rows each scanner flags, and the signed score it ranks them by (higher = stronger)."""
    events = events or set()
    q = lambda c, p: cs[c].quantile(p)                 # noqa: E731
    out = {
        "momentum": (cs["mom_12_1"] >= q("mom_12_1", .8), _z(cs["mom_12_1"])),
        "relative_strength": (cs["dist_52w_high"] >= q("dist_52w_high", .8), _z(cs["dist_52w_high"])),
        "low_volatility": (cs["vol_60"] <= q("vol_60", .2), _z(-cs["vol_60"])),
        "mean_reversion": (cs["ret_20d"] <= q("ret_20d", .1), _z(-cs["ret_20d"])),
        "breakout": ((cs["dist_52w_high"] >= -0.01) & (cs["adx_14"] > 25), _z(cs["adx_14"])),
        "volume_anomaly": ((cs["turnover"] >= 3 * cs["adv20"]), _z(cs["turnover"] / cs["adv20"])),
        "volatility_expansion": (cs["atr_pctile_250"] >= 0.9, _z(cs["atr_pctile_250"])),   # IC is negative
        "event": (cs["lineage"].isin(events), pd.Series(0.0, index=cs.index)),
    }
    from ..opportunity import engine as E
    try:
        comp = E.composite(cs)
        out["composite"] = (comp >= comp.quantile(.8), comp)
    except E.EngineError:
        pass
    if ep:
        v = cs["lineage"].map(ep)
        if v.notna().sum() > 50:
            out["fundamental_value"] = (v >= v.quantile(.8), _z(v))
    return out


def scan(cs: pd.DataFrame, as_of: date, *, events: set | None = None, ep: dict | None = None,
         capital: float = 1_000_000.0, costs: CostModel | None = None) -> list[Opportunity]:
    costs = costs or CostModel()
    out = []
    for name, (mask, score) in conditions(cs, events, ep).items():
        ic, t, status, source = EVIDENCE[name]
        h = HORIZON[name]
        for i in np.flatnonzero(mask.fillna(False).to_numpy()):
            r = cs.iloc[i]
            sigma_h = float(r["vol_60"]) * np.sqrt(h / 252.0)
            z = float(score.iloc[i]) if np.isfinite(score.iloc[i]) else 0.0
            exp_ret = SHRINK * ic * sigma_h * z if ic is not None else 0.0
            order = min(0.05 * capital, 0.10 * float(r["adv20"]))
            c = costs.round_trip_sized(order_value=order, adv_value=float(r["adv20"]),
                                       daily_vol=float(r["vol_60"]) / np.sqrt(252.0))["total"]
            pl = P.plan(ticker=str(r["ticker"]), price=float(r["price"]), vol_annual=float(r["vol_60"]),
                        adv_inr=float(r["adv20"]), capital_inr=capital,
                        style="position" if h >= 20 else "swing", atr_pct=float(r["atr_14_pct"]) if pd.notna(r["atr_14_pct"]) else None)
            out.append(Opportunity(
                instrument=str(r["lineage"]), ticker=str(r["ticker"]), as_of=as_of, horizon=h, type=name,
                expected_return=float(exp_ret), expected_risk=sigma_h, liquidity=float(r["adv20"]), cost=float(c),
                expected_net=float(exp_ret - c), evidence=status, evidence_t=t, source=source,
                expiry=_expiry(as_of, h), invalidation=float(pl["stop_price"]), price=float(r["price"]),
                features={"z": z, "mom_12_1": float(r["mom_12_1"]), "dist_52w_high": float(r["dist_52w_high"]),
                          "vol_60": float(r["vol_60"]), "ret_20d": float(r["ret_20d"])}))
    return out


def best_per_name(opps: list[Opportunity]) -> pd.DataFrame:
    """One row per ticker: the opportunity with the highest expected net, and every type that flagged it."""
    if not opps:
        return pd.DataFrame()
    df = pd.DataFrame([o.as_dict() for o in opps])
    types = df.groupby("ticker")["type"].apply(lambda s: ",".join(sorted(set(s))))
    best = df.sort_values("expected_net", ascending=False).drop_duplicates("ticker").set_index("ticker")
    best["flagged_by"] = types
    return best.reset_index()


SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    day VARCHAR, ticker VARCHAR, type VARCHAR, horizon INTEGER, expected_return DOUBLE, expected_risk DOUBLE,
    cost DOUBLE, expected_net DOUBLE, evidence VARCHAR, price DOUBLE, invalidation DOUBLE, expiry VARCHAR,
    realised DOUBLE, PRIMARY KEY (day, ticker, type));
"""


def save(con, opps: list[Opportunity], day: str) -> int:
    con.execute(SCHEMA)
    rows = [(day, o.ticker, o.type, o.horizon, o.expected_return, o.expected_risk, o.cost, o.expected_net,
             o.evidence, o.price, o.invalidation, o.expiry.isoformat(), None) for o in opps]
    if rows:
        con.executemany("INSERT OR REPLACE INTO opportunities VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def resolve(con, research_con) -> int:
    """Fill the realised return of every opportunity whose horizon has passed, from the price archive."""
    con.execute(SCHEMA)
    pending = con.execute("SELECT day, ticker, type, horizon FROM opportunities WHERE realised IS NULL").df()
    n = 0
    for (day, h), g in pending.groupby(["day", "horizon"]):
        cal = [r[0] for r in research_con.execute(
            "SELECT DISTINCT business_date FROM adjusted_prices WHERE business_date >= ? ORDER BY 1 LIMIT ?",
            [day, int(h) + 1]).fetchall()]
        if len(cal) <= h or str(cal[0]) != day:
            continue
        px = dict(research_con.execute("""
            SELECT a.ticker, b.close_adj / a.close_adj - 1 FROM adjusted_prices a
            JOIN adjusted_prices b ON b.lineage = a.lineage AND b.business_date = ?
            WHERE a.business_date = ? AND a.close_adj > 0""", [cal[h], cal[0]]).fetchall())
        uni = float(np.mean(list(px.values()))) if px else 0.0
        for r in g.itertuples():
            if r.ticker in px:
                con.execute("UPDATE opportunities SET realised = ? WHERE day = ? AND ticker = ? AND type = ?",
                            [px[r.ticker] - uni, day, r.ticker, r.type])
                n += 1
    return n


def scorecard_by_type(con) -> list[dict]:
    """Per scanner: how many opportunities resolved, what was expected, what was realised (excess over the
    universe mean), net of the stated cost - the honest test of every expected-return number on the board."""
    con.execute(SCHEMA)
    df = con.execute("SELECT type, expected_return, expected_net, cost, realised FROM opportunities WHERE realised IS NOT NULL").df()
    out = []
    for t, g in df.groupby("type"):
        net = g["realised"] - g["cost"]
        se = net.std(ddof=1) / np.sqrt(len(net)) if len(net) > 1 else float("nan")
        out.append({"type": t, "resolved": int(len(g)), "expected_gross": float(g["expected_return"].mean()),
                    "realised_gross": float(g["realised"].mean()), "realised_net": float(net.mean()), "se": float(se)})
    return out
