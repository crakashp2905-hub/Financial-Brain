"""What the system considered, not only what it traded.

A system that records only its trades learns from a sample it selected itself. It can measure how its
positions did; it can never measure what it declined. So a rule that systematically rejects good
opportunities is invisible to it, because the evidence that would expose the rule is exactly what the
rule threw away.

This records every candidate at every rebalance - the ones taken, the ones already held, and the far
larger number rejected, with *why* each was rejected - and then resolves what each one actually did.
The rejected rows are the point.

## The four questions it can answer that a trade ledger cannot

    false negatives    rejected, and then outperformed
    false positives    taken, and then underperformed
    correct skips      rejected, and then underperformed
    the cost of a rule what the book gave up by applying a filter, measured rather than assumed

The third is the one that makes the first meaningful. A rule that rejects some winners is not
necessarily broken - every rule does - and the question is whether it rejects them at a worse rate
than chance, which needs the correct skips in the denominator.

## Why the counterfactual is honest here

There is no execution counterfactual to guess at. A rejected name's forward return is a fact about
the market, not a simulation of a trade that never happened: it is what the name did over the window,
measured the same way the taken names are measured, against the same universe. What this cannot say is
what the *portfolio* would have done had it held the name instead of something else - that is a
different and much harder question, and :func:`report` does not pretend to answer it.

## Size

A top-100 book over a 1,700-name universe rejects sixteen names for every one it takes, so this table
grows at roughly the universe size times the number of rebalances. Eleven years at h60 is about 45
rebalances and 75,000 rows, which is small; at h20 with a wider universe it is not, so writes are
batched and ``record`` takes the whole rebalance at once rather than a row at a time.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

#: Dispositions. The rejections are separated because they are different failures: being ranked below
#: the cut is the strategy working as designed, while being dropped by a filter or starved of cash is
#: a rule or a constraint acting, and only the second kind can be reconsidered.
TAKEN = "TAKEN"            #: entered the book this rebalance
HELD = "HELD"              #: was already held and stayed
REJECTED_RANK = "REJECTED_RANK"      #: never held, ranked below the cut
REJECTED_FILTER = "REJECTED_FILTER"  #: never held, an exclusion fired
REJECTED_CASH = "REJECTED_CASH"      #: wanted, but the book ran out of money
#: Was held and is no longer wanted - a SELL. Kept apart from REJECTED_RANK because pooling exits
#: with the thousands of names never owned makes the one disposition that measures selling decisions
#: measure something else entirely.
EXITED = "EXITED"

REJECTIONS = (REJECTED_RANK, REJECTED_FILTER, REJECTED_CASH)
ACCEPTANCES = (TAKEN, HELD)


class MemoryError_(ValueError):
    pass


def record(con, *, run_id: str, session: date, signal_from: date,
           rows: list[dict]) -> int:
    """Write one rebalance's candidates. ``rows`` carries lineage, disposition and the rest.

    Replaces any existing rows for this (run, session) rather than appending, so re-running an
    experiment overwrites its own memory instead of doubling it. A run is identified by its
    experiment id, which already hashes the configuration.
    """
    if not rows:
        return 0
    bad = [r for r in rows if not r.get("lineage") or not r.get("disposition")]
    if bad:
        raise MemoryError_(f"{len(bad)} rows without a lineage or a disposition")

    con.execute("DELETE FROM opportunity_memory WHERE run_id = ? AND session = ?",
                [run_id, session])
    con.executemany("""
        INSERT INTO opportunity_memory
        (run_id, session, signal_from, lineage, rank, score, disposition, reason,
         fwd_return, fwd_excess, resolved_at, unresolvable)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)
    """, [[run_id, session, signal_from, r["lineage"], r.get("rank"), r.get("score"),
           r["disposition"], r.get("reason")] for r in rows])
    return len(rows)


def resolve(con, *, run_id: str, horizon: int) -> dict:
    """Fill in what each candidate actually did over ``horizon`` sessions after the rebalance.

    ``fwd_excess`` is measured against the mean of everything considered on that session, which is the
    right comparison: the question is not whether a rejected name went up but whether it went up more
    than the alternatives the ranking had in front of it at the time.

    A name with no price ``horizon`` sessions later is marked ``unresolvable`` rather than left NULL,
    so "the window has not closed" stays distinct from "this lineage stopped trading". Collapsing
    them quietly drops the delistings, which are exactly the names a rejection rule should be
    credited for avoiding.
    """
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _fwd AS
        WITH cand AS (
            SELECT DISTINCT session, lineage FROM opportunity_memory WHERE run_id = ?
        ), px AS (
            SELECT lineage, business_date, close_adj,
                   LEAD(close_adj, ?) OVER (PARTITION BY lineage ORDER BY business_date) AS fwd
            FROM adjusted_prices WHERE close_adj > 0
        )
        SELECT c.session, c.lineage, p.fwd / p.close_adj - 1 AS ret
        FROM cand c JOIN px p
          ON p.lineage = c.lineage AND p.business_date = c.session
    """, [run_id, horizon])

    con.execute("""
        CREATE OR REPLACE TEMP TABLE _bar AS
        SELECT session, AVG(ret) AS uni FROM _fwd WHERE ret IS NOT NULL GROUP BY session
    """)

    con.execute("""
        UPDATE opportunity_memory m
        SET fwd_return = f.ret,
            fwd_excess = f.ret - b.uni,
            resolved_at = ?,
            unresolvable = FALSE
        FROM _fwd f JOIN _bar b USING (session)
        WHERE m.run_id = ? AND m.session = f.session AND m.lineage = f.lineage
          AND f.ret IS NOT NULL
    """, [datetime.now(timezone.utc), run_id])

    # Anything still unresolved whose window has demonstrably closed is a name that stopped trading.
    con.execute("""
        UPDATE opportunity_memory m
        SET unresolvable = TRUE, resolved_at = ?
        WHERE m.run_id = ? AND m.fwd_return IS NULL AND m.unresolvable IS NULL
          AND (SELECT COUNT(*) FROM (SELECT DISTINCT business_date FROM adjusted_prices
                                     WHERE business_date > m.session)) >= ?
    """, [datetime.now(timezone.utc), run_id, horizon])

    row = con.execute("""
        SELECT COUNT(*),
               SUM(CASE WHEN fwd_return IS NOT NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN unresolvable THEN 1 ELSE 0 END),
               SUM(CASE WHEN fwd_return IS NULL AND unresolvable IS NULL THEN 1 ELSE 0 END)
        FROM opportunity_memory WHERE run_id = ?""", [run_id]).fetchone()
    return {"rows": row[0], "resolved": row[1] or 0, "unresolvable": row[2] or 0,
            "pending": row[3] or 0, "horizon": horizon,
            "why": "unresolvable means the window closed and the lineage had no price; pending "
                   "means the window has not closed yet"}


def report(con, *, run_id: str) -> dict:
    """What the rejections were worth.

    The headline is ``rejection_edge``: the mean forward excess of everything rejected, minus the mean
    of everything taken. A ranking that works makes this negative - the names it declined did worse
    than the names it took. Positive means the ranking is discarding the better half.
    """
    rows = con.execute("""
        SELECT disposition, COUNT(*), AVG(fwd_excess),
               SUM(CASE WHEN fwd_excess > 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN unresolvable THEN 1 ELSE 0 END)
        FROM opportunity_memory
        WHERE run_id = ? AND (fwd_excess IS NOT NULL OR unresolvable)
        GROUP BY disposition ORDER BY 2 DESC
    """, [run_id]).fetchall()
    if not rows:
        raise MemoryError_(f"no resolved rows for {run_id!r}; call resolve() first")

    by = {r[0]: {"n": r[1], "mean_excess": r[2], "winners": r[3] or 0,
                 "unresolvable": r[4] or 0,
                 "win_rate": ((r[3] or 0) / r[1]) if r[1] else None}
          for r in rows}

    def pooled(keys):
        ns = [by[k]["n"] for k in keys if k in by and by[k]["mean_excess"] is not None]
        ms = [by[k]["mean_excess"] for k in keys if k in by and by[k]["mean_excess"] is not None]
        tot = sum(ns)
        return (sum(n * m for n, m in zip(ns, ms)) / tot) if tot else None, tot

    taken_mean, taken_n = pooled(ACCEPTANCES)
    rej_mean, rej_n = pooled(REJECTIONS)

    # False negatives: rejected and then beat the field. Counted against the correct skips, because a
    # rule that rejects some winners is not broken - the question is the rate.
    fn = sum(by[k]["winners"] for k in REJECTIONS if k in by)
    fp = sum(by[k]["n"] - by[k]["winners"] for k in ACCEPTANCES if k in by)
    return {
        "run_id": run_id,
        "by_disposition": by,
        "taken_mean_excess": taken_mean, "taken_n": taken_n,
        "rejected_mean_excess": rej_mean, "rejected_n": rej_n,
        "rejection_edge": ((rej_mean - taken_mean)
                           if (rej_mean is not None and taken_mean is not None) else None),
        "false_negatives": fn,
        "false_negative_rate": (fn / rej_n) if rej_n else None,
        "false_positives": fp,
        "false_positive_rate": (fp / taken_n) if taken_n else None,
        "why": "rejection_edge is the mean forward excess of what was declined minus what was taken; "
               "a ranking that works makes it negative, and positive means the better half is being "
               "discarded",
    }


def by_reason(con, *, run_id: str) -> list[dict]:
    """The same question per rejection reason, which is where a bad rule becomes visible.

    A filter that fires on names which then outperform is costing the book directly, and the cost is
    the difference between what it rejected and what the book took instead.
    """
    rows = con.execute("""
        SELECT disposition, COALESCE(reason, '-'), COUNT(*), AVG(fwd_excess),
               SUM(CASE WHEN fwd_excess > 0 THEN 1 ELSE 0 END)
        FROM opportunity_memory
        WHERE run_id = ? AND fwd_excess IS NOT NULL AND disposition IN (?, ?, ?)
        GROUP BY 1, 2 HAVING COUNT(*) >= 20 ORDER BY 4 DESC
    """, [run_id, *REJECTIONS]).fetchall()
    return [{"disposition": r[0], "reason": r[1], "n": r[2], "mean_excess": r[3],
             "win_rate": (r[4] or 0) / r[2]} for r in rows]
