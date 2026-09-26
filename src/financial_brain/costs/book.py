"""Portfolio-level cost: what the *book* pays, not what the average name pays.

Every gate in this project charged one bucket to a whole portfolio - ``bucket="mid"``,
70.87 bps, applied to whatever the signal happened to hold. That is wrong in both
directions and the direction depends on where the signal's holdings sit in the liquidity
ranking, which is a property of the signal rather than a choice:

* the four intraday systems traded a set of names that is **62% mega and 38% large** and
  were charged ``mid`` on the **delivery** schedule, which over-stated their round trip
  roughly fourfold;
* every daily cross-sectional signal's top quintile is close to **half small or micro**,
  and ``mid`` *under*-states its round trip.

So the flat charge flattered the daily book and punished the intraday one. Nothing passed
either way, so no verdict moves in the permissive direction.

## The ranking population is the whole market, and getting that wrong is subtle

The bucket edges in ``costs.india`` are **absolute national ranks** - top 100 by turnover
is ``mega``, 101-300 ``large``, and so on - calibrated against the full priced NSE equity
list, about 3,660 names. Ranking inside any *filtered* population silently inflates every
name in it: with 1,200 eligible names, 62% of them fall inside rank 750 and are charged
``mid`` impact or better, when nationally most of them are ``small``. The first version of
this module ranked within ``features`` (~2,610 names) and the first patch to
``evaluation/timeseries`` ranked within its own liquidity-filtered universe (~1,200), and
both were optimistic for exactly that reason.

The ranking here is therefore always over ``universe_snapshots`` - the exchange's own list
for that date - and the rank is by the turnover observed **on** that date, so the cost a
strategy is charged comes from a ranking it could have read that morning. A cost charged
from a ranking the strategy could not have seen is look-ahead in the cost model, which is
the same error as look-ahead in the signal and much harder to notice, because the number it
produces is plausible either way.
"""
from __future__ import annotations

from .india import CostModel, Segment

#: Turnover-rank boundaries, matching ``universe.snapshot.liquidity_buckets``. Absolute
#: national ranks - see the module docstring on why the population must not be filtered.
EDGES = ((100, "mega"), (300, "large"), (750, "mid"), (1500, "small"))
#: Cheapest first; ``micro`` is everything past the last edge.
BUCKETS = tuple(b for _, b in EDGES) + ("micro",)

_VIEW = "_liquidity_buckets"


def ensure_view(con) -> str:
    """Materialise ``(d, lineage, bucket)`` for every date, once per connection.

    A view would recompute the ranking window on each join, and the ranking is 10M rows
    over 2,900 dates. This is a temp table so it cannot touch the stored schema.
    """
    if con.execute(f"""SELECT COUNT(*) FROM duckdb_tables()
                       WHERE table_name = '{_VIEW}' AND temporary""").fetchone()[0]:
        return _VIEW
    case = " ".join(f"WHEN rnk <= {n} THEN '{b}'" for n, b in EDGES)
    con.execute(f"""
        CREATE TEMP TABLE {_VIEW} AS
        SELECT u.business_date AS d, l.lineage,
               CASE {case} ELSE 'micro' END AS bucket
        FROM (SELECT business_date, isin,
                     ROW_NUMBER() OVER (PARTITION BY business_date
                                        ORDER BY turnover DESC NULLS LAST) AS rnk
              FROM universe_snapshots
              WHERE exchange = 'NSE' AND instrument_type = 'STK'
                AND turnover IS NOT NULL AND turnover > 0) u
        JOIN security_lineage l USING (isin)
    """)
    con.execute(f"CREATE INDEX {_VIEW}_k ON {_VIEW} (d, lineage)")
    return _VIEW


def round_trips(*, segment: Segment = Segment.DELIVERY, model: CostModel | None = None,
                include_impact: bool = True) -> dict[str, float]:
    """Round trip per bucket, as a fraction of notional."""
    m = model or CostModel()
    return {b: m.round_trip(turnover=1_000_000, segment=segment, bucket=b,
                            include_impact=include_impact)["bps"] / 10_000
            for b in BUCKETS}


def buckets(con, dates, lineages) -> dict[tuple, str]:
    """``(business_date, lineage) -> bucket`` from that date's national turnover ranking."""
    if not dates or not lineages:
        return {}
    v = ensure_view(con)
    rows = con.execute(f"""
        SELECT d, lineage, bucket FROM {v}
        WHERE d IN (SELECT UNNEST(?)) AND lineage IN (SELECT UNNEST(?))
    """, [list(dates), list(lineages)]).fetchall()
    return {(d, lin): b for d, lin, b in rows}


def per_rebalance(con, series, *, segment: Segment = Segment.DELIVERY,
                  model: CostModel | None = None,
                  include_impact: bool = True) -> list[float | None]:
    """Round-trip cost of each rebalance's holdings, one entry per element of ``series``.

    ``series`` is what ``benchmark.evaluate`` returns: each element carries the rebalance
    ``date`` and the ``top`` quintile's members. Equal weight, so the book's cost is the
    mean of its names' costs; a name with no bucket on that date is dropped from the mean
    rather than given a default - a default is a number nobody measured, which is how the
    flat charge got in - and a rebalance with no bucketed name returns ``None``.
    """
    rt = round_trips(segment=segment, model=model, include_impact=include_impact)
    want = buckets(con, [s["date"] for s in series],
                   {lin for s in series for lin in (s["top"] or [])})
    out: list[float | None] = []
    for s in series:
        costs = [rt[b] for lin in (s["top"] or [])
                 if (b := want.get((s["date"], lin))) is not None]
        out.append(sum(costs) / len(costs) if costs else None)
    return out


def mix(con, series) -> dict[str, float]:
    """Fraction of holdings in each bucket, across every rebalance. Diagnostic: it is what
    says *why* a signal's book is expensive, which a single cost number cannot."""
    want = buckets(con, [s["date"] for s in series],
                   {lin for s in series for lin in (s["top"] or [])})
    counts: dict[str, int] = {}
    for s in series:
        for lin in (s["top"] or []):
            b = want.get((s["date"], lin), "unranked")
            counts[b] = counts.get(b, 0) + 1
    total = sum(counts.values()) or 1
    return {b: n / total for b, n in sorted(counts.items(), key=lambda kv: -kv[1])}
