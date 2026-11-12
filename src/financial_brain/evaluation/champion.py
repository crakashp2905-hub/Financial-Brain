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

from datetime import date, datetime, timezone
from collections import Counter
from collections.abc import Sequence
import json

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

EXECUTABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS champion_executable_cohorts (
    day VARCHAR, strategy VARCHAR, cohort_id VARCHAR, n INTEGER, gross DOUBLE, cost DOUBLE, net DOUBLE,
    capital DOUBLE, selected_n INTEGER, missing_n INTEGER, complete BOOLEAN, metric_version VARCHAR,
    cash_days INTEGER, no_fill_n INTEGER, rejected_n INTEGER, partial_n INTEGER, start_day VARCHAR, end_day VARCHAR,
    PRIMARY KEY (day, strategy)
);
"""

UNIVERSE_SCHEMA = """
CREATE TABLE IF NOT EXISTS champion_universe_capture (
    day VARCHAR PRIMARY KEY, attempted_n INTEGER, strategies_n INTEGER, capture_status VARCHAR, source VARCHAR
);
CREATE TABLE IF NOT EXISTS champion_universe_attempts (
    day VARCHAR, strategy VARCHAR, isin VARCHAR, ticker VARCHAR, lineage VARCHAR, eligible BOOLEAN,
    selected BOOLEAN, reason VARCHAR, forward_return DOUBLE, price_path_status VARCHAR,
    corporate_action_ids VARCHAR, PRIMARY KEY (day, strategy, isin)
);
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
    con.execute("ALTER TABLE champion_cohorts ADD COLUMN IF NOT EXISTS attempted_n INTEGER")
    con.execute("ALTER TABLE champion_cohorts ADD COLUMN IF NOT EXISTS rejected_n INTEGER")
    con.execute("ALTER TABLE champion_cohorts ADD COLUMN IF NOT EXISTS missing_path_n INTEGER")
    con.execute("ALTER TABLE champion_cohorts ADD COLUMN IF NOT EXISTS corporate_action_n INTEGER")
    con.execute("ALTER TABLE champion_cohorts ADD COLUMN IF NOT EXISTS corporate_action_table_available BOOLEAN")


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


def _record_attempted_universe(research_con, ledger_con, day: date, eligible: pd.DataFrame,
                               books: dict[str, pd.DataFrame]) -> int:
    """Persist the point-in-time NSE EQ snapshot, including data rejects and non-selected names."""
    ledger_con.execute(UNIVERSE_SCHEMA)
    try:
        universe = research_con.execute("""
            SELECT u.isin, u.ticker, u.tradable, u.close_price, l.lineage, p.close_adj,
                   f.adv20, f.vol_60, f.mom_12_1, f.dist_52w_high, f.ret_20d
            FROM universe_snapshots u
            LEFT JOIN security_lineage l ON l.isin = u.isin
            LEFT JOIN adjusted_prices p ON p.lineage = l.lineage AND p.business_date = u.business_date
            LEFT JOIN features f ON f.lineage = l.lineage AND f.business_date = u.business_date
            WHERE u.business_date = ? AND UPPER(u.exchange) = 'NSE' AND UPPER(COALESCE(u.series, 'EQ')) = 'EQ'
              AND UPPER(COALESCE(u.instrument_type, 'STK')) = 'STK'
            ORDER BY u.isin, u.ticker
        """, [day]).df()
    except duckdb.CatalogException:
        ledger_con.execute("INSERT OR REPLACE INTO champion_universe_capture VALUES (?,?,?,?,?)",
                           [day.isoformat(), 0, len(books), "MISSING_SNAPSHOT_SCHEMA", "universe_snapshots"])
        return 0
    if universe.empty:
        ledger_con.execute("INSERT OR REPLACE INTO champion_universe_capture VALUES (?,?,?,?,?)",
                           [day.isoformat(), 0, len(books), "NO_SNAPSHOT_ROWS", "universe_snapshots"])
        return 0
    eligible_lineages = set(eligible["lineage"].dropna().astype(str))
    strategy_selections = {
        name: set(zip(frame["lineage"].astype(str), frame["ticker"].astype(str)))
        for name, frame in books.items()
    }
    rows = []
    for name in books:
        for item in universe.itertuples(index=False):
            lineage = str(item.lineage) if pd.notna(item.lineage) else None
            is_eligible = bool(lineage and lineage in eligible_lineages)
            is_selected = bool(is_eligible and lineage and
                               (lineage, str(item.ticker)) in strategy_selections[name])
            if not bool(item.tradable):
                reason = "NOT_TRADABLE"
            elif lineage is None:
                reason = "MISSING_LINEAGE"
            elif item.close_adj is None or pd.isna(item.close_adj) or float(item.close_adj) <= 0:
                reason = "MISSING_ADJUSTED_PRICE"
            elif item.adv20 is None or pd.isna(item.adv20):
                reason = "MISSING_ADV20"
            elif float(item.adv20) < 1e7:
                reason = "BELOW_MIN_ADV20"
            elif any(pd.isna(value) for value in (item.vol_60, item.mom_12_1, item.dist_52w_high, item.ret_20d)):
                reason = "MISSING_REQUIRED_FEATURE"
            elif float(item.vol_60) <= 0:
                reason = "NONPOSITIVE_VOLATILITY"
            elif is_selected:
                reason = "SELECTED"
            elif is_eligible:
                reason = "NOT_SELECTED_BY_STRATEGY"
            else:
                reason = "OUTSIDE_STRATEGY_CANDIDATE_SET"
            rows.append((day.isoformat(), name, str(item.isin), str(item.ticker), lineage, is_eligible,
                         is_selected, reason, None, "PENDING", "[]"))
    ledger_con.executemany("INSERT OR REPLACE INTO champion_universe_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    ledger_con.execute("INSERT OR REPLACE INTO champion_universe_capture VALUES (?,?,?,?,?)",
                       [day.isoformat(), int(universe["isin"].nunique()), len(books), "CAPTURED",
                        "universe_snapshots:NSE:EQ"])
    return int(universe["isin"].nunique())


def _filter_to_tradable_snapshot(research_con, day: date, cs: pd.DataFrame) -> pd.DataFrame:
    """When available, prevent suspended/non-tradable point-in-time names from entering strategy books."""
    try:
        lineages = research_con.execute("""
            SELECT DISTINCT l.lineage FROM universe_snapshots u
            JOIN security_lineage l ON l.isin = u.isin
            WHERE u.business_date = ? AND UPPER(u.exchange) = 'NSE'
              AND UPPER(COALESCE(u.series, 'EQ')) = 'EQ'
              AND UPPER(COALESCE(u.instrument_type, 'STK')) = 'STK' AND u.tradable = TRUE
        """, [day]).fetchdf()["lineage"].dropna()
    except duckdb.CatalogException:
        return cs
    return cs[cs["lineage"].isin(set(lineages.astype(str)))].reset_index(drop=True)


def record(research_con, ledger_con, day: date, *, costs: CostModel | None = None) -> dict:
    """Write each strategy's picks for ``day`` before any outcome exists. Refuses days before the lock."""
    if day < LOCK_DATE:
        raise ChampionError(f"{day} is before the lock ({LOCK_DATE}); it cannot count as out of sample")
    ledger_con.execute(SCHEMA)
    costs = costs or CostModel()
    cs = _filter_to_tradable_snapshot(research_con, day, S.cross_section(research_con, day))
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
    n["universe_attempts"] = _record_attempted_universe(research_con, ledger_con, day, cs, books)
    return n


def _resolve_universe_attempts(ledger_con, research_con, day: str, end_day: date,
                               fwd: dict[str, float]) -> dict[str, dict]:
    """Attach observed path/action evidence and conservative missing-path bounds to attempts."""
    attempts = ledger_con.execute("""
        SELECT strategy, isin, lineage, selected FROM champion_universe_attempts WHERE day = ?
    """, [day]).df()
    if attempts.empty:
        return {}
    action_data_available = True
    actions_by_lineage: dict[str, list[str]] = {}
    try:
        action_rows = research_con.execute("""
            SELECT l.lineage, a.action_id FROM corporate_actions a
            JOIN security_lineage l ON l.isin = a.isin
            WHERE a.ex_date >= ? AND a.ex_date <= ? ORDER BY l.lineage, a.action_id
        """, [date.fromisoformat(day), end_day]).fetchall()
        for lineage, action_id in action_rows:
            actions_by_lineage.setdefault(str(lineage), []).append(str(action_id))
    except duckdb.CatalogException:
        action_data_available = False
    lineage_counts = attempts.dropna(subset=["lineage"]).groupby(["strategy", "lineage"]).size().to_dict()
    metrics: dict[str, dict] = {}
    for attempt in attempts.itertuples(index=False):
        lineage = str(attempt.lineage) if pd.notna(attempt.lineage) else None
        is_ambiguous = bool(lineage and lineage_counts.get((attempt.strategy, lineage), 0) > 1)
        ret = fwd.get(lineage) if lineage and not is_ambiguous else None
        status = "OBSERVED" if ret is not None else ("AMBIGUOUS_LINEAGE" if is_ambiguous else "MISSING_PRICE_PATH")
        recorded_action_ids = actions_by_lineage.get(lineage, []) if lineage else []
        ledger_con.execute("""UPDATE champion_universe_attempts
            SET forward_return=?, price_path_status=?, corporate_action_ids=?
            WHERE day=? AND strategy=? AND isin=?""",
            [ret, status, json.dumps(recorded_action_ids, separators=(",", ":")), day,
             attempt.strategy, attempt.isin])
    for strategy, group in attempts.groupby("strategy"):
        returns = []
        missing = 0
        unique_action_ids: set[str] = set()
        for attempt in group.itertuples(index=False):
            lineage = str(attempt.lineage) if pd.notna(attempt.lineage) else None
            duplicate = bool(lineage and lineage_counts.get((strategy, lineage), 0) > 1)
            ret = fwd.get(lineage) if lineage and not duplicate else None
            if ret is None:
                missing += 1
            else:
                returns.append(float(ret))
            unique_action_ids.update(actions_by_lineage.get(lineage, []) if lineage else [])
        attempted_n = len(group)
        metrics[str(strategy)] = {
            "attempted_n": attempted_n,
            "rejected_n": int((~group["selected"].fillna(False).astype(bool)).sum()),
            "missing_path_n": missing,
            "corporate_action_n": len(unique_action_ids) if action_data_available else None,
            "corporate_action_table_available": action_data_available,
            "universe": float(np.mean(returns)) if returns else None,
            "coverage": (attempted_n - missing) / attempted_n if attempted_n else None,
            "worst_case": (sum(returns) - missing) / attempted_n if attempted_n else None,
        }
    return metrics


def resolve(ledger_con, research_con, horizon: int = HORIZON) -> int:
    """Score matured cohorts, retaining every missing selected outcome explicitly.

    Missing names are not assigned an invented realised return. The -100% bound
    is a sensitivity, and an incomplete cohort is ineligible for promotion.
    """
    ledger_con.execute(SCHEMA)
    _cohort_columns(ledger_con)
    ledger_con.execute(UNIVERSE_SCHEMA)
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
        attempt_metrics = _resolve_universe_attempts(ledger_con, research_con, str(day), cal[horizon], fwd)
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
            m = attempt_metrics.get(str(strat), {})
            ledger_con.execute("""INSERT OR REPLACE INTO champion_cohorts
                (day, strategy, n, gross, cost, net, universe, selected_n, missing_n, worst_case_net,
                 complete, universe_coverage, universe_worst_case, metric_version, attempted_n, rejected_n,
                 missing_path_n, corporate_action_n, corporate_action_table_available)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [day, strat, int(len(valid)), gross, cost, gross - cost if gross is not None else None,
                 m.get("universe", uni), selected_n, missing_n, worst, missing_n == 0,
                 m.get("coverage", universe_coverage), m.get("worst_case", universe_worst), RANKING_METRIC,
                 m.get("attempted_n"), m.get("rejected_n"), m.get("missing_path_n"),
                 m.get("corporate_action_n"), m.get("corporate_action_table_available")])
        done += 1
    return done


def resolve_executable(
    ledger_con,
    *,
    scheduled: pd.DataFrame,
    exchange_sessions: Sequence[str | date],
    session_evidence: pd.DataFrame,
    orders: pd.DataFrame,
    fills: pd.DataFrame,
    marks: pd.DataFrame,
    horizon: int = HORIZON,
) -> int:
    """Resolve frozen paper cohorts from observed order, fill, session, and mark evidence.

    ``scheduled`` has ``cohort_id, day, strategy, capital``. ``session_evidence`` has one
    ``(cohort_id, day)`` row for every exchange session in each cohort, including sessions
    where the strategy stayed in cash; rows must have ``complete=True`` and
    ``corporate_actions_checked=True``. ``orders`` records every attempt and terminal states
    (FILLED, NO_FILL, REJECTED, CANCELED/CANCELLED, EXPIRED); ``fills`` records actual fills,
    timestamps, and fees. Open PARTIAL orders do not resolve. ``marks`` contains daily marks with
    source and UTC observation time. Values are
    normalized to account return, and cash days therefore contribute zero instead of vanishing
    from the scheduled denominator.

    This function deliberately does not synthesize fills or marks. A missing fact makes that
    cohort incomplete and ineligible for promotion. Production still needs to connect the
    runner's durable journal to these normalized inputs.
    """
    if horizon < 1:
        raise ChampionError("horizon must be positive")

    def require_columns(frame: pd.DataFrame, required: set[str], name: str) -> None:
        if not isinstance(frame, pd.DataFrame) or not required.issubset(frame.columns):
            missing = sorted(required - set(frame.columns if isinstance(frame, pd.DataFrame) else ()))
            raise ChampionError(f"{name} missing required columns: {missing}")

    def session_day(value: str | date) -> str:
        try:
            return date.fromisoformat(str(value)).isoformat()
        except (TypeError, ValueError) as exc:
            raise ChampionError(f"invalid exchange session date: {value!r}") from exc

    def utc_instant(value: object, label: str) -> datetime:
        try:
            instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ChampionError(f"{label} must be a timezone-aware UTC timestamp") from exc
        if instant.tzinfo is None or instant.utcoffset() != timezone.utc.utcoffset(instant):
            raise ChampionError(f"{label} must be a timezone-aware UTC timestamp")
        return instant

    require_columns(scheduled, {"cohort_id", "day", "strategy", "capital"}, "scheduled")
    require_columns(session_evidence, {"cohort_id", "day", "complete", "corporate_actions_checked"},
                    "session_evidence")
    require_columns(orders, {"cohort_id", "order_id", "strategy", "day", "submitted_at_utc", "symbol", "side",
                             "requested_quantity", "status"}, "orders")
    require_columns(fills, {"order_id", "fill_id", "day", "filled_at_utc", "quantity", "price", "fees"}, "fills")
    require_columns(marks, {"day", "symbol", "price", "source", "observed_at_utc"}, "marks")

    calendar = [session_day(day) for day in exchange_sessions]
    if calendar != sorted(set(calendar)) or not calendar:
        raise ChampionError("exchange_sessions must be unique, ordered session dates")
    calendar_index = {day: i for i, day in enumerate(calendar)}
    frozen = scheduled.copy()
    frozen["day"] = frozen["day"].map(session_day)
    if frozen.empty or frozen["cohort_id"].isna().any() or frozen["cohort_id"].duplicated().any():
        raise ChampionError("scheduled cohorts must have unique, non-empty cohort_id values")
    if frozen["strategy"].isna().any() or (frozen["strategy"].astype(str).str.len() == 0).any():
        raise ChampionError("scheduled strategy identifiers must be non-empty")
    frozen = frozen.sort_values(["strategy", "day", "cohort_id"], kind="stable")
    last_end: dict[str, int] = {}
    for row in frozen.itertuples(index=False):
        if row.day not in calendar_index or date.fromisoformat(row.day) < LOCK_DATE:
            raise ChampionError("scheduled cohort day must be a calendar session on/after the lock")
        start = calendar_index[row.day]
        end = start + horizon
        if end >= len(calendar):
            raise ChampionError(f"cohort {row.cohort_id} has not reached its scheduled endpoint")
        if start <= last_end.get(str(row.strategy), -1):
            raise ChampionError(f"overlapping scheduled cohorts for strategy {row.strategy}")
        last_end[str(row.strategy)] = end
        try:
            capital = float(row.capital)
        except (TypeError, ValueError) as exc:
            raise ChampionError("cohort capital must be a positive finite number") from exc
        if not np.isfinite(capital) or capital <= 0:
            raise ChampionError("cohort capital must be a positive finite number")

    evidence = session_evidence.copy()
    evidence["day"] = evidence["day"].map(session_day)
    if evidence.duplicated(["cohort_id", "day"]).any():
        raise ChampionError("session evidence contains duplicate cohort/day rows")
    order_rows = orders.copy()
    order_rows["day"] = order_rows["day"].map(session_day)
    if order_rows["order_id"].isna().any() or order_rows["order_id"].duplicated().any():
        raise ChampionError("order ids must be present and unique")
    order_rows["submitted_at"] = order_rows["submitted_at_utc"].map(
        lambda value: utc_instant(value, "submitted_at_utc"))
    fill_rows = fills.copy()
    fill_rows["day"] = fill_rows["day"].map(session_day)
    if fill_rows["fill_id"].isna().any() or fill_rows["fill_id"].duplicated().any():
        raise ChampionError("fill ids must be present and unique")
    fill_rows["filled_at"] = fill_rows["filled_at_utc"].map(
        lambda value: utc_instant(value, "filled_at_utc"))
    mark_rows = marks.copy()
    mark_rows["day"] = mark_rows["day"].map(session_day)
    if mark_rows.duplicated(["day", "symbol"]).any():
        raise ChampionError("marks contain duplicate day/symbol rows")
    mark_prices: dict[tuple[str, str], float] = {}
    for mark in mark_rows.itertuples(index=False):
        try:
            price = float(mark.price)
            utc_instant(mark.observed_at_utc, "observed_at_utc")
        except (TypeError, ValueError) as exc:
            raise ChampionError("marks require a finite positive price and UTC observation time") from exc
        if not np.isfinite(price) or price <= 0:
            raise ChampionError("marks require a finite positive price and UTC observation time")
        if not str(mark.source).strip():
            raise ChampionError("marks require a source identifier")
        mark_prices[(mark.day, str(mark.symbol))] = price

    order_ids = set(order_rows["order_id"].astype(str))
    if not set(fill_rows["order_id"].astype(str)).issubset(order_ids):
        raise ChampionError("fill references an unknown order")
    ledger_con.execute(SCHEMA)
    _cohort_columns(ledger_con)
    ledger_con.execute(EXECUTABLE_SCHEMA)
    ledger_con.execute("ALTER TABLE champion_executable_cohorts ADD COLUMN IF NOT EXISTS no_fill_n INTEGER")
    ledger_con.execute("ALTER TABLE champion_executable_cohorts ADD COLUMN IF NOT EXISTS rejected_n INTEGER")
    ledger_con.execute("ALTER TABLE champion_executable_cohorts ADD COLUMN IF NOT EXISTS partial_n INTEGER")
    scheduled_ids = set(frozen["cohort_id"].astype(str))
    if not set(order_rows["cohort_id"].astype(str)).issubset(scheduled_ids):
        raise ChampionError("order references an unscheduled cohort")
    processed = 0
    terminal = {"FILLED", "NO_FILL", "REJECTED", "CANCELED", "CANCELLED", "EXPIRED"}
    for cohort in frozen.itertuples(index=False):
        cid, strategy, start_day = str(cohort.cohort_id), str(cohort.strategy), cohort.day
        start = calendar_index[start_day]
        days = calendar[start:start + horizon + 1]
        end_day = days[-1]
        cap = float(cohort.capital)
        ev = evidence[evidence["cohort_id"].astype(str) == cid].set_index("day")
        day_rows = order_rows[order_rows["cohort_id"].astype(str) == cid]
        cohort_fills = fill_rows[fill_rows["order_id"].astype(str).isin(set(day_rows["order_id"].astype(str)))]
        invalid = len(ev) != len(days) or set(ev.index) != set(days)
        if not invalid:
            invalid = not bool(ev.loc[days, "complete"].eq(True).all()) or not bool(
                ev.loc[days, "corporate_actions_checked"].eq(True).all())
        invalid = invalid or (not day_rows.empty and not (day_rows["strategy"].astype(str) == strategy).all())
        invalid = invalid or (not day_rows.empty and not day_rows["day"].isin(days).all())
        invalid = invalid or (not cohort_fills.empty and not cohort_fills["day"].isin(days).all())
        invalid = invalid or (not day_rows.empty and not day_rows["status"].astype(str).str.upper().isin(terminal).all())
        fills_by_order = {str(k): v for k, v in cohort_fills.groupby(cohort_fills["order_id"].astype(str))}
        cash = cap
        gross_cash = cap
        inventory: dict[str, int] = {}
        gross_inventory: dict[str, int] = {}
        inventory_by_day: dict[str, dict[str, int]] = {}
        gross_inventory_by_day: dict[str, dict[str, int]] = {}
        fees_total = 0.0
        invalid_orders = False
        fill_events = []
        for order in day_rows.itertuples(index=False):
            status = str(order.status).upper()
            side = str(order.side).upper()
            if side not in {"BUY", "SELL"} or int(order.requested_quantity) <= 0:
                invalid_orders = True
                continue
            fs = fills_by_order.get(str(order.order_id))
            quantity = 0
            if fs is not None:
                for fill in fs.itertuples(index=False):
                    qty, price, fee = int(fill.quantity), float(fill.price), float(fill.fees)
                    if qty <= 0 or not np.isfinite(price) or price <= 0 or not np.isfinite(fee) or fee < 0:
                        invalid_orders = True
                        continue
                    if fill.day < order.day:
                        invalid_orders = True
                    if fill.filled_at < order.submitted_at:
                        invalid_orders = True
                    if fill.filled_at.date().isoformat() != fill.day:
                        invalid_orders = True
                    quantity += qty
                    fill_events.append((fill.filled_at, fill.day, order, qty, price, fee, side))
            if quantity > int(order.requested_quantity):
                invalid_orders = True
            if status == "FILLED" and quantity != int(order.requested_quantity):
                invalid_orders = True
            if status in {"NO_FILL", "REJECTED", "CANCELLED", "EXPIRED"} and quantity != 0:
                if status in {"NO_FILL", "REJECTED"} or quantity > int(order.requested_quantity):
                    invalid_orders = True
            if status == "CANCELED" and quantity > int(order.requested_quantity):
                invalid_orders = True
        for _, fill_day, order, qty, price, fee, side in sorted(fill_events, key=lambda event: event[0]):
            notional = qty * price
            signed = -notional if side == "BUY" else notional
            gross_cash += signed
            cash += signed - fee
            fees_total += fee
            current = inventory.get(str(order.symbol), 0)
            if side == "SELL" and qty > current:
                invalid_orders = True
            inventory[str(order.symbol)] = current + qty if side == "BUY" else current - qty
            gross_current = gross_inventory.get(str(order.symbol), 0)
            gross_inventory[str(order.symbol)] = gross_current + qty if side == "BUY" else gross_current - qty
            inventory_by_day[fill_day] = dict(inventory)
            gross_inventory_by_day[fill_day] = dict(gross_inventory)
        invalid = invalid or invalid_orders or cash < -0.005
        gross_equity, net_equity = gross_cash, cash
        day_inventory: dict[str, int] = {}
        day_gross_inventory: dict[str, int] = {}
        for d in days:
            day_inventory = inventory_by_day.get(d, day_inventory)
            day_gross_inventory = gross_inventory_by_day.get(d, day_gross_inventory)
            for symbol, quantity in day_inventory.items():
                if quantity and (d, symbol) not in mark_prices:
                    invalid = True
            for symbol, quantity in day_gross_inventory.items():
                if quantity and (d, symbol) not in mark_prices:
                    invalid = True
        for symbol, quantity in inventory.items():
            if quantity:
                net_equity += quantity * mark_prices.get((end_day, symbol), 0.0)
        for symbol, quantity in gross_inventory.items():
            if quantity:
                gross_equity += quantity * mark_prices.get((end_day, symbol), 0.0)
        prior = ledger_con.execute("SELECT cohort_id FROM champion_executable_cohorts WHERE day=? AND strategy=?",
                                   [start_day, strategy]).fetchone()
        if prior is not None:
            raise ChampionError(f"cohort {cid} already has a resolved row; refusing to overwrite evidence")
        cohort_orders = len(day_rows)
        filled_orders = sum(1 for order_id in fills_by_order if len(fills_by_order[order_id]))
        order_fill_qty = {str(order.order_id): sum(int(fill.quantity) for fill in fills_by_order.get(
            str(order.order_id), pd.DataFrame(columns=["quantity"])).itertuples(index=False))
            for order in day_rows.itertuples(index=False)}
        no_fill_orders = sum(1 for order in day_rows.itertuples(index=False)
                             if str(order.status).upper() in {"NO_FILL", "CANCELED", "CANCELLED", "EXPIRED"}
                             and order_fill_qty[str(order.order_id)] == 0)
        rejected_orders = sum(1 for order in day_rows.itertuples(index=False)
                             if str(order.status).upper() == "REJECTED")
        partial_orders = sum(1 for order in day_rows.itertuples(index=False)
                             if 0 < order_fill_qty[str(order.order_id)] < int(order.requested_quantity))
        filled_days = set(cohort_fills["day"].astype(str))
        cash_days = sum(1 for d in days if d not in filled_days)
        complete = not invalid
        gross = gross_equity / cap - 1.0 if complete else None
        cost = fees_total / cap if complete else None
        net = net_equity / cap - 1.0 if complete else None
        ledger_con.execute("""INSERT INTO champion_executable_cohorts
            (day, strategy, cohort_id, n, gross, cost, net, capital, selected_n, missing_n,
             complete, metric_version, cash_days, no_fill_n, rejected_n, partial_n, start_day, end_day)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [start_day, strategy, cid, filled_orders, gross, cost, net, cap, cohort_orders,
             int(invalid), complete, EXECUTABLE_METRIC, cash_days, no_fill_orders, rejected_orders,
             partial_orders, start_day, end_day])
        processed += 1
    return processed


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
    ledger_con.execute(EXECUTABLE_SCHEMA)
    df = ledger_con.execute("""
        SELECT c.day, c.strategy, c.net, c.complete, c.metric_version
        FROM champion_cohorts c
        WHERE NOT EXISTS (SELECT 1 FROM champion_executable_cohorts e
                          WHERE e.day=c.day AND e.strategy=c.strategy)
        UNION ALL
        SELECT day, strategy, net, complete, metric_version FROM champion_executable_cohorts
    """).df()
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
