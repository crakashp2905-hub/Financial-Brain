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

The current resolver is a close-to-close ranking diagnostic (``ranking_close_v1``),
not an executable next-session return. It is excluded from promotion regardless
of apparent performance. Until an independently verified, scheduled, executable
metric exists, the status remains insufficient evidence.
"""
from __future__ import annotations

from datetime import date
from collections import Counter
from collections.abc import Sequence

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
RANKING_METRIC = "ranking_close_v1"
EXECUTABLE_METRIC = "executable_next_session_v1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS champion_picks (day VARCHAR, strategy VARCHAR, ticker VARCHAR, lineage VARCHAR,
    cost DOUBLE, PRIMARY KEY (day, strategy, ticker));
CREATE TABLE IF NOT EXISTS champion_cohorts (day VARCHAR, strategy VARCHAR, n INTEGER, gross DOUBLE, cost DOUBLE,
    net DOUBLE, universe DOUBLE, PRIMARY KEY (day, strategy));
"""


class ChampionError(ValueError):
    pass


def _cohort_columns(con) -> None:
    """Add coverage without changing legacy seven-column inserts or old snapshots."""
    con.execute("ALTER TABLE champion_cohorts ADD COLUMN IF NOT EXISTS selected_n INTEGER")
    con.execute("ALTER TABLE champion_cohorts ADD COLUMN IF NOT EXISTS missing_n INTEGER")
    con.execute("ALTER TABLE champion_cohorts ADD COLUMN IF NOT EXISTS worst_case_net DOUBLE")
    con.execute("ALTER TABLE champion_cohorts ADD COLUMN IF NOT EXISTS complete BOOLEAN")
    con.execute("ALTER TABLE champion_cohorts ADD COLUMN IF NOT EXISTS universe_coverage DOUBLE")
    con.execute("ALTER TABLE champion_cohorts ADD COLUMN IF NOT EXISTS universe_worst_case DOUBLE")
    con.execute("ALTER TABLE champion_cohorts ADD COLUMN IF NOT EXISTS metric_version VARCHAR")


#: Value and quality cannot join yet: exchange XBRL results stop at the quarter ended Dec 2024 (later results moved
#: to NSE's "Integrated Filing" feed, not yet ingested), so a value or quality book today would rank on figures up
#: to 18 months stale - not a fair contest. They join when that feed is covered.
WAITING_FOR_DATA = ("value_ep", "quality_margin", "earnings_surprise")


def picks(cs: pd.DataFrame, *, as_of: date, events: set | None = None, swing: pd.DataFrame | None = None,
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
    best = S.best_per_name(S.scan(cs, as_of))
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
    books = picks(cs, as_of=day, events=events, swing=swing, learned=learned)
    for name, df in books.items():
        if df["lineage"].isna().any() or df["lineage"].duplicated().any():
            raise ChampionError(f"{name}: missing or duplicate security lineage")
    for name, df in books.items():
        rows = []
        for r in df.itertuples():
            c = costs.round_trip_sized(order_value=min(5e5, 0.1 * r.adv20), adv_value=r.adv20,
                                       daily_vol=r.vol_60 / np.sqrt(252.0))["total"]
            rows.append((day.isoformat(), name, r.ticker, r.lineage, float(c)))
        if rows:                                         # a challenger with nothing to hold that day records nothing
            ledger_con.executemany("INSERT OR REPLACE INTO champion_picks VALUES (?,?,?,?,?)", rows)
        n[name] = len(rows)
    return n


def resolve(ledger_con, research_con, horizon: int = HORIZON) -> int:
    """Score matured cohorts, retaining every missing selected outcome explicitly.

    Missing names are not assigned an invented realised return. The -100% bound
    is a sensitivity, and an incomplete cohort is ineligible for promotion.
    """
    ledger_con.execute(SCHEMA)
    _cohort_columns(ledger_con)
    days = [r[0] for r in ledger_con.execute(
        """SELECT DISTINCT day FROM champion_picks WHERE day NOT IN (SELECT DISTINCT day FROM champion_cohorts)""").fetchall()]
    done = 0
    for day in days:
        cal = [r[0] for r in research_con.execute(
            "SELECT DISTINCT business_date FROM adjusted_prices WHERE business_date >= ? ORDER BY 1 LIMIT ?",
            [day, horizon + 1]).fetchall()]
        if len(cal) <= horizon or str(cal[0]) != day:
            continue
        fwd_rows = research_con.execute("""
            SELECT a.lineage, b.close_adj / a.close_adj - 1 FROM adjusted_prices a
            JOIN adjusted_prices b ON b.lineage = a.lineage AND b.business_date = ?
            WHERE a.business_date = ? AND a.close_adj > 0 AND b.close_adj > 0""",
            [cal[horizon], cal[0]]).fetchall()
        multiplicity = Counter(lineage for lineage, _ in fwd_rows)
        fwd = {lineage: ret for lineage, ret in fwd_rows if multiplicity[lineage] == 1}
        start_n = research_con.execute("""SELECT COUNT(DISTINCT lineage) FROM adjusted_prices
                                          WHERE business_date = ? AND close_adj > 0""",
                                       [cal[0]]).fetchone()[0]
        universe_coverage = len(fwd) / start_n if start_n else None
        universe_worst = ((sum(fwd.values()) - (start_n - len(fwd))) / start_n
                          if start_n else None)
        uni = float(np.mean(list(fwd.values()))) if fwd else None
        for strat, g in ledger_con.execute(
                "SELECT strategy, lineage, cost FROM champion_picks WHERE day = ?", [day]).df().groupby("strategy"):
            observed = g["lineage"].map(fwd)
            # A legacy book may contain the same security under multiple ticker
            # aliases. Neither alias gets an invented independent position return.
            observed[g["lineage"].duplicated(keep=False)] = np.nan
            valid = observed.dropna()
            selected_n, missing_n = len(g), int(observed.isna().sum())
            gross = float(valid.mean()) if len(valid) else None
            cost = float(g["cost"].mean())
            worst = float((valid.sum() - missing_n) / selected_n - cost)
            ledger_con.execute("""INSERT OR REPLACE INTO champion_cohorts
                (day, strategy, n, gross, cost, net, universe, selected_n, missing_n, worst_case_net,
                 complete, universe_coverage, universe_worst_case, metric_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [day, strat, int(len(valid)), gross, cost, gross - cost if gross is not None else None,
                 uni, selected_n, missing_n, worst, missing_n == 0,
                 universe_coverage, universe_worst, RANKING_METRIC])
        done += 1
    return done


def standings(ledger_con, horizon: int = HORIZON,
              eligible_days: set[str] | None = None,
              scheduled_days: Sequence[str | date] | None = None) -> list[dict]:
    """Pair only predeclared rebalance dates; observed rows never choose the schedule.

    ``scheduled_days`` must be a frozen list of non-overlapping *exchange-session*
    entry dates supplied by the caller's registered protocol. The ledger cannot
    infer missed dates or attest them after the fact. No schedule or external
    verification means no promotion-grade cohorts, regardless of returns.
    """
    if horizon < 1:
        raise ChampionError("horizon must be positive")
    ledger_con.execute(SCHEMA)
    _cohort_columns(ledger_con)
    df = ledger_con.execute("SELECT day, strategy, net, complete, metric_version FROM champion_cohorts").df()
    if df.empty:
        return [{"strategy": s, "status": "INSUFFICIENT_DATA", "cohorts": 0} for s in ("composite_top",)]
    strategies = sorted(set(df["strategy"]) - {CHAMPION}) or ["composite_top"]
    if scheduled_days is None:
        return [{"strategy": s, "status": "INSUFFICIENT_EVIDENCE", "cohorts": 0,
                 "diagnostic_rows": int((df["strategy"] == s).sum()),
                 "reason": "no precommitted exchange-session rebalance schedule"} for s in strategies]
    schedule = [str(d) for d in scheduled_days]
    if schedule != sorted(set(schedule)) or any(date.fromisoformat(d) < LOCK_DATE for d in schedule):
        raise ChampionError("scheduled_days must be unique, ordered dates after the lock")
    rows = {(r.day, r.strategy): r for r in df.itertuples(index=False)}
    verified = eligible_days or set()
    out = []
    for s in strategies:
        differences = []
        diagnostic = withheld = legacy = incomplete = non_executable = 0
        for day in schedule:
            candidate, champion = rows.get((day, s)), rows.get((day, CHAMPION))
            if candidate is None or champion is None:
                withheld += 1
                continue
            if pd.isna(candidate.complete) or pd.isna(champion.complete):
                legacy += 1
                withheld += 1
                continue
            if not bool(candidate.complete) or not bool(champion.complete) or pd.isna(candidate.net) or pd.isna(champion.net):
                incomplete += 1
                withheld += 1
                continue
            diagnostic += 1
            if candidate.metric_version != EXECUTABLE_METRIC or champion.metric_version != EXECUTABLE_METRIC:
                non_executable += 1
                withheld += 1
                continue
            if day in verified:
                differences.append(float(candidate.net - champion.net))
            else:
                withheld += 1
        d = pd.Series(differences, dtype=float)
        n = len(d)
        t = float(d.mean() / (d.std(ddof=1) / np.sqrt(n))) if n > 2 and d.std(ddof=1) > 0 else float("nan")
        if n < MIN_COHORTS:
            status = "INSUFFICIENT_DATA"
        elif t >= T_PROMOTE and withheld == 0:
            status = "PROMOTE"
        elif t >= T_PROMOTE:
            status = "INSUFFICIENT_EVIDENCE"
        elif t <= -T_PROMOTE:
            status = "WORSE_THAN_CHAMPION"
        else:
            status = "NOT_DISTINGUISHABLE"
        out.append({"strategy": s, "cohorts": n, "diagnostic_cohorts": diagnostic,
                    "scheduled_cohorts": len(schedule), "withheld_cohorts": withheld,
                    "legacy_cohorts": legacy, "incomplete_cohorts": incomplete,
                    "non_executable_cohorts": non_executable,
                    "mean_net_vs_champion": float(d.mean()) if n else float("nan"),
                    "t": t, "status": status})
    return out
