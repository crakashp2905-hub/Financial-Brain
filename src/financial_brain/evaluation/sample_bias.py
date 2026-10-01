"""Is the sample a candidate generator produces representative of the opportunity set?

``evaluation/replay.py`` says in its own comment that it is not a strategy backtest: a committee run
costs a minute of local inference, so it looks at the names each session *surfaced* rather than the
names available. That is a reasonable engineering compromise and it has a consequence nobody had
measured - every result the replay has ever produced is measured on a sample the replay chose, and if
that sample is systematically different from the market then the results do not transfer to it.

The question, stated the way it has to be answered:

    did the system do well because it selected good opportunities,
    or because it was only ever shown a particular kind of opportunity?

## The selector is three filters, not one

    materiality = 'high'                         a filing the exchange flagged as material
    event_type IN (7 categories)                 results, order wins, ratings, and four more
    ORDER BY filings DESC LIMIT per_day          the BUSIEST names that session

The third is the one that gets missed. Ordering by filing count and taking the top two selects names
having an unusually eventful day, which is a different population again - and it is the layer least
likely to have been intended as a selection at all.

So each layer is measured separately against the full eligible universe on the same sessions. A single
"the sample is biased by X%" number would hide which filter did it and therefore what to change.

## The statistic

Same discipline as :mod:`.events`: the effect is a per-session difference between the selected names'
forward excess and everything else's, and the t-statistic is computed over **sessions**, because
filings cluster in time - results season, a regulatory sweep - and treating every selected name as an
independent observation inflates the t by roughly the names per session.

Forward excess is measured against the mean of the full eligible universe over the same window, so a
sample that merely holds high-beta names in a rising market does not read as a good sample.
"""
from __future__ import annotations

import math
from datetime import date

#: The event types ``replay.CANDIDATE_SQL`` admits, copied here rather than imported so that a change
#: to the replay's own list shows up as a difference between the two rather than silently changing
#: what this module reports it measured.
REPLAY_EVENT_TYPES = ("ORDER_WIN", "RESULTS", "LEGAL_REGULATORY", "ACQUISITION",
                      "MANAGEMENT_CHANGE", "CREDIT_RATING", "DIVIDEND")

#: Sessions with both a selected and an unselected group needed before a layer is reported.
MIN_SESSIONS = 30

#: Take every Nth session rather than all of them. The statistic is computed over sessions, so a
#: fifth of 2,676 is still 535 observations - far past what the t needs - while the pool it has to
#: build is a fifth the size. The full-session version did not finish: it joins a window over 4.9M
#: price rows to 4.9M feature rows. Striding is a sampling choice, not an approximation of one, and
#: the session count is always reported so the reader can see what it bought.
DEFAULT_STRIDE = 5


class BiasError(ValueError):
    pass


def _layers(per_day: int) -> list[tuple[str, str]]:
    """(name, SQL predicate over ``announcements a``) for each successive filter, and the composite.

    Cumulative on purpose: layer 2 includes layer 1. The interesting number is what each filter adds,
    and that is the difference between consecutive rows rather than each row on its own.
    """
    types = ", ".join(f"'{t}'" for t in REPLAY_EVENT_TYPES)
    return [
        ("any filing", "TRUE"),
        ("+ materiality high", "a.materiality = 'high'"),
        (f"+ one of {len(REPLAY_EVENT_TYPES)} event types",
         f"a.materiality = 'high' AND a.event_type IN ({types})"),
        (f"+ busiest {per_day} that session",
         f"a.materiality = 'high' AND a.event_type IN ({types})"),
    ]


def prepare(con, *, horizon: int, start: date, end: date, min_adv: float = 1e7,
            stride: int = DEFAULT_STRIDE) -> str:
    """Materialise the eligible pool and its forward returns once, for all layers to share.

    Each layer is a different filter over the *same* pool, so recomputing it per layer recomputes a
    window function over 4.9M price rows and a join against 4.9M feature rows every time. Eight
    layer-and-horizon combinations did that eight times and the whole measurement did not finish.
    Built once per horizon, it is one scan.
    """
    # The dates are made distinct BEFORE they are numbered. Numbering first and de-duplicating after
    # numbers every price row, so with 80 names a session the row numbers jump by 80 per date, a fifth
    # of them survive the modulo, and _sessions ends up holding sixteen copies of every date - which
    # fans the pool out sixteen times and leaves every count wrong while the per-session averages
    # still look plausible.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _sessions AS
        WITH days AS (
            SELECT DISTINCT business_date FROM adjusted_prices
            WHERE business_date BETWEEN ? AND ?
        ), numbered AS (
            SELECT business_date, ROW_NUMBER() OVER (ORDER BY business_date) AS rn FROM days
        )
        SELECT business_date FROM numbered WHERE (rn - 1) % ? = 0
    """, [start, end, max(int(stride), 1)])

    con.execute("""
        CREATE OR REPLACE TEMP TABLE _pool AS
        WITH fwd AS (
            -- Bounded by date, and the end extended by the horizon so the last sessions in the
            -- window still have a forward price to reach.
            SELECT lineage, business_date,
                   LEAD(close_adj, ?) OVER (PARTITION BY lineage ORDER BY business_date)
                     / close_adj - 1 AS ret
            FROM adjusted_prices
            WHERE close_adj > 0 AND business_date >= ?
              AND business_date <= CAST(? AS DATE) + INTERVAL 400 DAY
        )
        SELECT f.business_date, f.lineage, w.ret
        FROM features f
        JOIN _sessions d ON d.business_date = f.business_date
        JOIN fwd w ON w.lineage = f.lineage AND w.business_date = f.business_date
        WHERE f.adv20 >= ? AND w.ret IS NOT NULL
    """, [horizon, start, end, min_adv])
    return "_pool"


def layer(con, *, predicate: str, horizon: int = 20, start: date, end: date,
          min_adv: float = 1e7, top_n: int | None = None,
          pool_ready: bool = False, stride: int = DEFAULT_STRIDE) -> dict:
    """Forward excess of the names a predicate selects, against everything else it did not.

    ``top_n`` additionally keeps only the busiest ``top_n`` names per session, which is the layer the
    replay applies through ``ORDER BY filings DESC`` and the one most likely to be accidental.

    ``pool_ready`` says :func:`prepare` has already built the shared pool for this horizon and window.
    Passing it when the pool was built for a *different* horizon silently measures the wrong forward
    return, so :func:`replay_sample` owns both calls and callers should normally go through it.
    """
    if not pool_ready:
        prepare(con, horizon=horizon, start=start, end=end, min_adv=min_adv, stride=stride)

    rank_clause = ""
    if top_n:
        rank_clause = f"""
            , ranked AS (
                SELECT lineage, business_date FROM (
                    SELECT s.lineage, s.business_date,
                           ROW_NUMBER() OVER (PARTITION BY s.business_date
                                              ORDER BY s.filings DESC, s.lineage) AS rn
                    FROM selected s)
                WHERE rn <= {int(top_n)}
            )"""
    selected_from = "ranked" if top_n else "selected"

    rows = con.execute(f"""
        WITH selected AS (
            SELECT p.business_date, l.lineage, COUNT(*) AS filings
            FROM announcements a
            JOIN security_lineage l ON l.isin = a.isin
            JOIN _pool p ON p.lineage = l.lineage AND p.business_date = a.business_date
            WHERE a.isin IS NOT NULL AND ({predicate})
            GROUP BY p.business_date, l.lineage
        ){rank_clause}
        , marked AS (
            -- A semi-join, not a LEFT JOIN. Joining to the selected set multiplies a pool row by
            -- however many selected rows it matches, and it inflated both the selected count and the
            -- pool count by the same factor - so the per-session average looked plausible while
            -- every count was wrong. EXISTS cannot fan out.
            SELECT p.business_date, p.ret,
                   EXISTS (SELECT 1 FROM {selected_from} s
                           WHERE s.lineage = p.lineage
                             AND s.business_date = p.business_date) AS picked
            FROM _pool p
        )
        SELECT business_date,
               AVG(CASE WHEN picked THEN ret END) AS sel,
               AVG(CASE WHEN NOT picked THEN ret END) AS rest,
               SUM(CASE WHEN picked THEN 1 ELSE 0 END) AS n_sel,
               COUNT(*) AS n_pool
        FROM marked
        GROUP BY business_date
        HAVING SUM(CASE WHEN picked THEN 1 ELSE 0 END) > 0
           AND SUM(CASE WHEN NOT picked THEN 1 ELSE 0 END) >= 20
        ORDER BY business_date
    """).fetchall()

    if len(rows) < MIN_SESSIONS:
        return {"sessions": len(rows), "mean_difference": None, "t": None,
                "why": f"{len(rows)} usable sessions is below {MIN_SESSIONS}"}

    diffs = [r[1] - r[2] for r in rows]
    n = len(diffs)
    m = sum(diffs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in diffs) / (n - 1))
    selected_n = sum(r[3] for r in rows)
    pool_n = sum(r[4] for r in rows)
    return {
        "sessions": n,
        "selected_names": selected_n,
        "selected_per_session": selected_n / n,
        "pool_per_session": pool_n / n,
        "share_of_pool": selected_n / pool_n if pool_n else None,
        "mean_selected": sum(r[1] for r in rows) / n,
        "mean_rest": sum(r[2] for r in rows) / n,
        "mean_difference": m,
        "t": (m / (sd / math.sqrt(n))) if sd > 0 else None,
        "differences": diffs,
        "dates": [r[0] for r in rows],
        "why": "the difference is per-session between the selected names and the rest of the "
               "eligible universe; the t is over sessions because filings cluster in time",
    }


def replay_sample(con, *, horizon: int = 20, start: date, end: date,
                  per_day: int = 2, min_adv: float = 1e7,
                  stride: int = DEFAULT_STRIDE, progress=None) -> dict:
    """Measure each of the replay selector's filters against the full opportunity set.

    Returns one row per cumulative layer. The number that matters is the last one: whether the sample
    the replay actually runs on behaves like the market it is supposed to generalise to.
    """
    prepare(con, horizon=horizon, start=start, end=end, min_adv=min_adv, stride=stride)
    out = []
    specs = _layers(per_day)
    for i, (name, predicate) in enumerate(specs):
        top = per_day if i == len(specs) - 1 else None
        r = layer(con, predicate=predicate, horizon=horizon, start=start, end=end,
                  min_adv=min_adv, top_n=top, pool_ready=True)
        r["layer"] = name
        r["top_n"] = top
        out.append(r)
        if progress:
            progress(r)

    usable = [r for r in out if r.get("t") is not None]
    final = out[-1] if out[-1].get("t") is not None else None
    return {
        "layers": out,
        "final": final,
        "representative": (abs(final["t"]) < 2.0) if final else None,
        "horizon": horizon,
        "window": [str(start), str(end)],
        "stride": stride,
        "per_day": per_day,
        "incremental": [
            {"layer": b["layer"],
             "adds": (b["mean_difference"] - a["mean_difference"])
                     if (a.get("mean_difference") is not None
                         and b.get("mean_difference") is not None) else None}
            for a, b in zip(usable, usable[1:])],
        "why": "each layer is cumulative, so what a filter ADDS is the difference between "
               "consecutive rows; a sample whose |t| exceeds 2 is not a random draw from the "
               "opportunity set and results measured on it do not transfer to it",
    }
