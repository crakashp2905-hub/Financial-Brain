"""Champion and challengers, judged only on data no research has seen.

The project's most important finding is that **naive momentum beats the machinery built around it**. So naive
momentum is the champion, and nothing replaces it for looking sophisticated. Every challenger - the composite,
low volatility, relative strength, "everything but the worst 20%", the scanners' best net alpha - is recorded
side by side with the champion every session, and judged on outcomes that did not exist when the rule was
written.

## The lock

``LOCK_DATE`` is 2026-10-05. Every study in this repository used prices up to 2026-10-01. From the lock on, each
strategy's picks are written down **before** their outcomes exist, which makes everything after the lock a true
out-of-sample record. Nothing before it counts toward promotion.

## Promotion, mechanically

A challenger replaces the champion only when all of these hold on the locked record:

1. at least ``MIN_COHORTS`` non-overlapping 20-session cohorts (about a year) have resolved;
2. its mean net return beats the champion's, cohort by cohort, at ``t >= T_PROMOTE``;
3. it does so **after** its own sized round-trip costs (each cohort pays one full round trip);
4. its stated expected return is not worse calibrated than the champion's (realised/stated slope).

Until then the status is ``INSUFFICIENT_DATA`` - which is the honest status for every challenger today.
"""
from __future__ import annotations

from datetime import date

import duckdb
import numpy as np
import pandas as pd

from ..costs.india import CostModel
from ..opportunity import engine as E
from ..opportunity import scanners as S

LOCK_DATE = date(2026, 10, 5)
HORIZON = 20
TOP_FRACTION = 0.2
MIN_COHORTS = 12
T_PROMOTE = 2.0
CHAMPION = "naive_momentum"

SCHEMA = """
CREATE TABLE IF NOT EXISTS champion_picks (day VARCHAR, strategy VARCHAR, ticker VARCHAR, lineage VARCHAR,
    cost DOUBLE, PRIMARY KEY (day, strategy, ticker));
CREATE TABLE IF NOT EXISTS champion_cohorts (day VARCHAR, strategy VARCHAR, n INTEGER, gross DOUBLE, cost DOUBLE,
    net DOUBLE, universe DOUBLE, PRIMARY KEY (day, strategy));
"""


class ChampionError(ValueError):
    pass


#: Value and quality cannot join yet: exchange XBRL results stop at the quarter ended Dec 2024 (later results moved
#: to NSE's "Integrated Filing" feed, not yet ingested), so a value or quality book today would rank on figures up
#: to 18 months stale - not a fair contest. They join when that feed is covered.
WAITING_FOR_DATA = ("value_ep", "quality_margin", "earnings_surprise")


def picks(cs: pd.DataFrame, *, events: set | None = None, swing: pd.DataFrame | None = None,
          learned: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    """Each strategy's book for a session: the rows it would hold. ``events``: lineages with a material filing on
    the previous session; ``swing``: the swing book's ranking (both optional, joined from 2026-10-06)."""
    if len(cs) < 100:
        raise ChampionError(f"{len(cs)} names is too few")
    c = cs.copy()
    c["z"] = E.composite(c)
    q = lambda col, p: c[col].quantile(p)            # noqa: E731
    out = {
        CHAMPION: c[c["mom_12_1"] >= q("mom_12_1", 1 - TOP_FRACTION)],
        "composite_top": c[c["z"] >= q("z", 1 - TOP_FRACTION)],
        "low_volatility": c[c["vol_60"] <= q("vol_60", TOP_FRACTION)],
        "relative_strength": c[c["dist_52w_high"] >= q("dist_52w_high", 1 - TOP_FRACTION)],
        "all_but_worst_20": c[c["z"] > q("z", 0.2)],
    }
    from ..opportunity import ranking_v2 as R2
    c["v2"] = R2.score(c).to_numpy()
    out["ranking_v2_top"] = c[c["v2"] >= q("v2", 1 - TOP_FRACTION)]    # joined 2026-10-07 (PRE-REGISTERED ranking v2)
    if learned is not None and len(learned):
        top = learned.head(max(1, int(TOP_FRACTION * len(learned))))      # joined 2026-10-07 (Result of the deep feature library)
        out["learned_model_top"] = c[c["lineage"].isin(set(top["lineage"]))]
    if events:
        out["event_names"] = c[c["lineage"].isin(events)]
    if swing is not None and len(swing):
        out["swing_top50"] = c[c["ticker"].isin(set(swing.head(50)["ticker"]))]
    best = S.best_per_name(S.scan(cs, LOCK_DATE))
    if not best.empty:
        top = best.sort_values("expected_net", ascending=False).head(max(1, int(TOP_FRACTION * len(c))))
        out["scanner_best_net"] = c[c["ticker"].isin(set(top["ticker"]))]
    return out


def record(research_con, ledger_con, day: date, *, costs: CostModel | None = None) -> dict:
    """Write each strategy's picks for ``day`` before any outcome exists. Refuses days before the lock."""
    if day < LOCK_DATE:
        raise ChampionError(f"{day} is before the lock ({LOCK_DATE}); it cannot count as out of sample")
    ledger_con.execute(SCHEMA)
    costs = costs or CostModel()
    cs = S.cross_section(research_con, day)
    try:
        events = {r[0] for r in research_con.execute("""
            SELECT DISTINCT l.lineage FROM announcements a JOIN security_lineage l ON l.isin = a.isin
            WHERE a.business_date = ? AND a.materiality IN ('HIGH', 'CRITICAL')""", [day]).fetchall()}
    except duckdb.CatalogException:                     # a database without filings: no event book that day
        events = set()
    from ..paper import book as BK
    try:
        swing = BK.swing_ranking(research_con, day)
    except Exception:                                   # noqa: BLE001 - the swing list is optional
        swing = None
    try:
        from . import learned_model as LM
        learned = LM.score_day(research_con, day)
    except Exception:                                   # noqa: BLE001 - no trained model yet, or a short history
        learned = None
    n = {}
    for name, df in picks(cs, events=events, swing=swing, learned=learned).items():
        rows = []
        for r in df.itertuples():
            c = costs.round_trip_sized(order_value=min(5e5, 0.1 * r.adv20), adv_value=r.adv20,
                                       daily_vol=r.vol_60 / np.sqrt(252.0))["total"]
            rows.append((day.isoformat(), name, r.ticker, r.lineage, float(c)))
        ledger_con.executemany("INSERT OR REPLACE INTO champion_picks VALUES (?,?,?,?,?)", rows)
        n[name] = len(rows)
    return n


def resolve(ledger_con, research_con, horizon: int = HORIZON) -> int:
    """Score every cohort whose horizon has passed: gross, cost, net, and the universe mean for reference."""
    ledger_con.execute(SCHEMA)
    days = [r[0] for r in ledger_con.execute(
        """SELECT DISTINCT day FROM champion_picks WHERE day NOT IN (SELECT DISTINCT day FROM champion_cohorts)""").fetchall()]
    done = 0
    for day in days:
        cal = [r[0] for r in research_con.execute(
            "SELECT DISTINCT business_date FROM adjusted_prices WHERE business_date >= ? ORDER BY 1 LIMIT ?",
            [day, horizon + 1]).fetchall()]
        if len(cal) <= horizon or str(cal[0]) != day:
            continue
        fwd = dict(research_con.execute("""
            SELECT a.lineage, b.close_adj / a.close_adj - 1 FROM adjusted_prices a
            JOIN adjusted_prices b ON b.lineage = a.lineage AND b.business_date = ?
            WHERE a.business_date = ? AND a.close_adj > 0""", [cal[horizon], cal[0]]).fetchall())
        if not fwd:
            continue
        uni = float(np.mean(list(fwd.values())))
        for strat, g in ledger_con.execute(
                "SELECT strategy, lineage, cost FROM champion_picks WHERE day = ?", [day]).df().groupby("strategy"):
            r = g["lineage"].map(fwd).dropna()
            if r.empty:
                continue
            gross, cost = float(r.mean()), float(g.loc[r.index, "cost"].mean())
            ledger_con.execute("INSERT OR REPLACE INTO champion_cohorts VALUES (?,?,?,?,?,?,?)",
                               [day, strat, int(len(r)), gross, cost, gross - cost, uni])
        done += 1
    return done


def standings(ledger_con, horizon: int = HORIZON) -> list[dict]:
    """Every challenger against the champion on non-overlapping cohorts (every ``horizon``-th recorded day)."""
    ledger_con.execute(SCHEMA)
    df = ledger_con.execute("SELECT day, strategy, net FROM champion_cohorts").df()
    if df.empty:
        return [{"strategy": s, "status": "INSUFFICIENT_DATA", "cohorts": 0} for s in ("composite_top",)]
    w = df.pivot_table(index="day", columns="strategy", values="net").sort_index()
    w = w.iloc[::horizon]                               # non-overlapping cohorts only
    out = []
    for s in w.columns:
        if s == CHAMPION:
            continue
        d = (w[s] - w[CHAMPION]).dropna() if CHAMPION in w.columns else pd.Series(dtype=float)
        n = len(d)
        t = float(d.mean() / (d.std(ddof=1) / np.sqrt(n))) if n > 2 and d.std(ddof=1) > 0 else float("nan")
        if n < MIN_COHORTS:
            status = "INSUFFICIENT_DATA"
        elif t >= T_PROMOTE:
            status = "PROMOTE"
        elif t <= -T_PROMOTE:
            status = "WORSE_THAN_CHAMPION"
        else:
            status = "NOT_DISTINGUISHABLE"
        out.append({"strategy": s, "cohorts": n, "mean_net_vs_champion": float(d.mean()) if n else float("nan"),
                    "t": t, "status": status})
    return out
