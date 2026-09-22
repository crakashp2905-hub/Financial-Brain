"""Historical analogues: what happened last time this happened (Phase 3).

A thesis needs a distribution, and the honest place to get one is the record. Asking a
language model for "probability of the bull case" produces a number with no provenance and
no way to be wrong; counting what followed the same kind of event, in this market, before
today, produces one that can be checked.

    this event type, as of this date
            -> every prior instance that had already resolved
            -> the forward excess return distribution
            -> bear / base / bull as terciles, with their empirical weights

Three rules keep it usable rather than flattering:

* **Point in time.** Only instances whose horizon closed on or before ``as_of`` are
  counted, so scenarios built for a 2021 decision cannot contain 2024's outcomes.
* **A benchmark you could buy.** Excess is measured against the equal-weighted universe
  over the *same* entry and exit sessions - the mean, never the median. See
  [[Beating the median is not an edge]] for the day that lesson cost.
* **Enough cases or nothing.** Below ``MIN_CASES`` this returns no scenarios at all,
  which makes the decision fail its arithmetic and become a NO TRADE. An invented
  distribution is worse than an absent one, because it passes.
"""
from __future__ import annotations

from datetime import date, timedelta

MIN_CASES = 30              # below this the terciles are noise
HORIZON_DAYS = 90
LOOKBACK_YEARS = 8
MIN_TURNOVER = 1e7          # Rs 1cr at entry, the same liquidity floor the firewall uses
MATERIALITY = "high"        # the population a decision is drawn from; see `outcomes`

_SQL = """
WITH ev AS (
    SELECT DISTINCT a.isin, a.business_date AS d
    FROM announcements a
    WHERE a.event_type = ? AND a.materiality = ? AND a.isin IS NOT NULL
      AND a.business_date >= ? AND a.business_date <= ?
), entry AS (
    SELECT ev.isin,
           (SELECT MIN(p.business_date) FROM adjusted_prices p
            WHERE p.isin = ev.isin AND p.business_date >= ev.d) AS entry_date
    FROM ev
), pairs AS (
    SELECT e.isin, e.entry_date,
           (SELECT MIN(p.business_date) FROM adjusted_prices p
            WHERE p.business_date >= e.entry_date + INTERVAL (?) DAY) AS exit_date
    FROM entry e WHERE e.entry_date IS NOT NULL
), spans AS (
    SELECT DISTINCT entry_date, exit_date FROM pairs
    WHERE exit_date IS NOT NULL AND exit_date <= ?
), market AS (
    SELECT s.entry_date, s.exit_date, AVG(b.close_adj / a.close_adj - 1) AS mkt
    FROM spans s
    JOIN adjusted_prices a ON a.business_date = s.entry_date AND a.close_adj > 0
    JOIN adjusted_prices b ON b.isin = a.isin AND b.business_date = s.exit_date
    GROUP BY 1, 2
)
SELECT (b.close_adj / a.close_adj - 1) - m.mkt AS excess
FROM pairs pr
JOIN market m ON m.entry_date = pr.entry_date AND m.exit_date = pr.exit_date
JOIN adjusted_prices a ON a.isin = pr.isin AND a.business_date = pr.entry_date
JOIN adjusted_prices b ON b.isin = pr.isin AND b.business_date = pr.exit_date
WHERE a.close_adj > 0 AND COALESCE(a.turnover, 0) >= ?
"""


def outcomes(con, event_type: str, as_of: date, *, horizon_days: int = HORIZON_DAYS,
             min_turnover: float = MIN_TURNOVER, materiality: str = MATERIALITY,
             lookback_years: int = LOOKBACK_YEARS) -> list[float]:
    """Forward excess returns after every prior instance of this event type.

    ``materiality`` is not a tuning knob - it has to match the population the decision is
    drawn from. The committee only ever looks up a distribution for a *high*-materiality
    filing, so a distribution built over every routine compliance notice of the same type
    would describe a different population than the trade it is sizing.
    """
    start = as_of - timedelta(days=365 * lookback_years)
    rows = con.execute(_SQL, [event_type, materiality, start, as_of, horizon_days, as_of,
                              min_turnover]).fetchall()
    return [float(r[0]) for r in rows if r[0] is not None]


def scenarios(con, event_type: str, as_of: date, **kw) -> dict | None:
    """Bear / base / bull from the record, or None when the record is too thin.

    The terciles carry near-equal probability by construction: the information is in the
    *returns*, not in weights someone chose.
    """
    got = sorted(outcomes(con, event_type, as_of, **kw))
    return terciles(got)


def terciles(got: list[float]) -> dict | None:
    """Split a sorted return sample into three weighted scenarios, or refuse."""
    n = len(got)
    if n < MIN_CASES:
        return None
    got = sorted(got)
    cut = n // 3
    bands = {"bear": got[:cut], "base": got[cut:n - cut], "bull": got[n - cut:]}
    return {name: {"probability": round(len(xs) / n, 3),
                   "return": round(sum(xs) / len(xs), 4),
                   "cases": len(xs)}
            for name, xs in bands.items()}


def summary(con, event_type: str, as_of: date, **kw) -> dict:
    """The distribution and how much evidence stands behind it."""
    got = sorted(outcomes(con, event_type, as_of, **kw))
    n = len(got)
    if not n:
        return {"event_type": event_type, "cases": 0, "usable": False, "scenarios": None}
    return {"event_type": event_type, "cases": n, "usable": n >= MIN_CASES,
            "mean_excess": sum(got) / n, "median_excess": got[n // 2],
            "win_rate": sum(1 for x in got if x > 0) / n,
            "worst": got[0], "best": got[-1],
            "scenarios": terciles(got)}


def catalogue(con, as_of: date, **kw) -> list[dict]:
    """Every event type with enough history to size a trade on, best first."""
    types = [r[0] for r in con.execute(
        "SELECT DISTINCT event_type FROM announcements WHERE event_type IS NOT NULL"
    ).fetchall()]
    out = [summary(con, t, as_of, **kw) for t in types]
    return sorted([s for s in out if s["cases"]],
                  key=lambda s: (s["usable"], s.get("mean_excess", 0)), reverse=True)


# --- cache -------------------------------------------------------------------
#
# A fresh distribution scans eleven years of announcements and costs about a minute, which
# is fine once a month and impossible inside a committee run. The cache is keyed by the
# *month* of ``as_of`` rather than the day: a distribution over thousands of cases does not
# move materially in a fortnight, and a monthly key keeps the point-in-time property
# (a decision in March can only ever read a distribution built from data up to March).

CACHE_DDL = """
CREATE TABLE IF NOT EXISTS analogue_distributions (
    event_type   VARCHAR,
    as_of_month  DATE,
    horizon_days INTEGER,
    cases        BIGINT,
    mean_excess  DOUBLE,
    scenarios    VARCHAR,
    built_at     TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (event_type, as_of_month, horizon_days)
)
"""


def _month(d: date) -> date:
    return d.replace(day=1)


def cached(con, event_type: str, as_of: date, *,
           horizon_days: int = HORIZON_DAYS, build: bool = True, **kw) -> dict | None:
    """The scenario set for this event type, computed at most once a month.

    Returns None when the history is too thin - which is the answer that turns into a
    NO TRADE downstream, and is cached like any other so a thin type is not rescanned
    on every run.
    """
    import json
    from datetime import datetime, timezone

    con.execute(CACHE_DDL)
    key = [event_type, _month(as_of), horizon_days]
    row = con.execute("""SELECT scenarios FROM analogue_distributions
                         WHERE event_type = ? AND as_of_month = ? AND horizon_days = ?""",
                      key).fetchone()
    if row is not None:
        return json.loads(row[0]) if row[0] else None
    if not build:
        return None

    got = sorted(outcomes(con, event_type, as_of, horizon_days=horizon_days, **kw))
    sc = terciles(got)
    con.execute("""INSERT OR REPLACE INTO analogue_distributions VALUES (?,?,?,?,?,?,?)""",
                key + [len(got), (sum(got) / len(got)) if got else None,
                       json.dumps(sc) if sc else None, datetime.now(timezone.utc)])
    return sc


def warm(con, as_of: date, *, min_events: int = 200, **kw) -> list[dict]:
    """Build the cache for every event type with enough filings to be worth scanning."""
    types = [r[0] for r in con.execute(
        """SELECT event_type FROM announcements WHERE event_type IS NOT NULL
           GROUP BY 1 HAVING COUNT(*) >= ? ORDER BY COUNT(*) DESC""",
        [min_events]).fetchall()]
    built = []
    for t in types:
        sc = cached(con, t, as_of, **kw)
        built.append({"event_type": t, "usable": sc is not None, "scenarios": sc})
    return built
