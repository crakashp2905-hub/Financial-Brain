"""C03 - Point-in-time observation store.

**The one component whose cost rises with every month of delay.**

No Indian vendor sells affordable point-in-time fundamentals. Screener, Moneycontrol and
most aggregators serve *restated, as-of-today* figures: restatements folded back into
history, the reporting date absent (only the period end, which in India lags the
announcement by 30-60 days), and ratios recomputed with today's share count. Any
fundamental backtest built on them contains silent look-ahead bias.

So we generate our own. The rule is one line and it is absolute:

    **Append only. Nothing is ever updated or deleted.**

A restatement is a *new row* with a later ``observed_at`` pointing at the row it revises.
``as_of()`` then reconstructs exactly what was knowable on any past date.

The four timestamps (docs/ARCHITECTURE.md):
    event_time    when it happened
    published_at  when it became public   <- the PIT anchor for backtests
    observed_at   when Financial-Brain saw it
    (as-of time is the query parameter, not a column)
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from ..config import TIER


@dataclass
class Observation:
    entity_key: str                  # ISIN, index name, macro series code
    attribute: str                   # revenue, eps, roce, repo_rate, ...
    entity_type: str = "security"
    value_num: float | None = None
    value_text: str | None = None
    unit: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    event_time: datetime | None = None
    published_at: datetime | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "MANUAL"
    evidence_key: str | None = None
    revision_of: str | None = None
    notes: str | None = None

    @property
    def source_tier(self) -> int:
        return TIER.get(self.source.upper(), 4)

    def identity(self) -> str:
        """Stable id from the fact's identity, so re-ingesting the same fact is a no-op."""
        basis = "|".join(str(x) for x in (
            self.entity_type, self.entity_key, self.attribute,
            self.period_start, self.period_end, self.value_num, self.value_text,
            self.published_at, self.source,
        ))
        return hashlib.sha256(basis.encode()).hexdigest()[:24]


class PITStore:
    """Append-only point-in-time fact store."""

    def __init__(self, con):
        self.con = con

    # -------------------------------------------------------------- writing
    def record(self, obs: Observation) -> str:
        """Append one observation. Returns its id. Idempotent on identical facts."""
        oid = obs.identity()
        existing = self.con.execute(
            "SELECT observation_id FROM pit_observations WHERE observation_id = ?", [oid]
        ).fetchone()
        if existing:
            return oid

        self.con.execute(
            """INSERT INTO pit_observations
               (observation_id, entity_type, entity_key, attribute, period_start, period_end,
                value_num, value_text, unit, event_time, published_at, observed_at,
                source, source_tier, evidence_key, revision_of, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [oid, obs.entity_type, obs.entity_key, obs.attribute, obs.period_start,
             obs.period_end, obs.value_num, obs.value_text, obs.unit, obs.event_time,
             obs.published_at, obs.observed_at, obs.source.upper(), obs.source_tier,
             obs.evidence_key, obs.revision_of, obs.notes],
        )
        return oid

    def record_many(self, observations: list[Observation]) -> list[str]:
        return [self.record(o) for o in observations]

    def revise(self, prior_id: str, obs: Observation) -> str:
        """Record a restatement. The prior row is retained, untouched."""
        obs.revision_of = prior_id
        return self.record(obs)

    # -------------------------------------------------------------- reading
    def as_of(self, as_of_time: datetime, *, entity_key: str | None = None,
              attribute: str | None = None, entity_type: str = "security") -> list[dict]:
        """What was knowable at ``as_of_time``.

        Filters on **published_at** (not observed_at) so a backtest sees a fact only once
        the market could have seen it. Rows with no published_at fall back to observed_at,
        which is conservative: we never claim to have known something earlier than we did.

        When a fact was restated, the latest revision *published before the cutoff* wins.
        """
        sql = """
            WITH visible AS (
                SELECT *,
                       COALESCE(published_at, observed_at) AS known_at
                FROM pit_observations
                WHERE entity_type = ?
                  AND COALESCE(published_at, observed_at) <= ?
            ), ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY entity_key, attribute, period_end
                    ORDER BY known_at DESC, source_tier ASC, observed_at DESC
                ) AS rn
                FROM visible
            )
            SELECT observation_id, entity_key, attribute, period_start, period_end,
                   value_num, value_text, unit, published_at, observed_at,
                   source, source_tier, evidence_key, revision_of
            FROM ranked WHERE rn = 1
        """
        params: list = [entity_type, as_of_time]
        if entity_key:
            sql += " AND entity_key = ?"
            params.append(entity_key)
        if attribute:
            sql += " AND attribute = ?"
            params.append(attribute)
        sql += " ORDER BY entity_key, attribute, period_end DESC"

        cur = self.con.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def history(self, entity_key: str, attribute: str) -> list[dict]:
        """Every version of one fact, oldest first - the restatement trail."""
        cur = self.con.execute("""
            SELECT observation_id, period_end, value_num, value_text,
                   published_at, observed_at, source, source_tier, revision_of, notes
            FROM pit_observations
            WHERE entity_key = ? AND attribute = ?
            ORDER BY COALESCE(published_at, observed_at), observed_at
        """, [entity_key, attribute])
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def restatements(self) -> list[dict]:
        """Facts that were later revised - a data-quality signal in its own right."""
        cur = self.con.execute("""
            SELECT entity_key, attribute, period_end, COUNT(*) AS versions,
                   MIN(COALESCE(published_at, observed_at)) AS first_known,
                   MAX(COALESCE(published_at, observed_at)) AS last_known
            FROM pit_observations
            GROUP BY entity_key, attribute, period_end
            HAVING COUNT(*) > 1
            ORDER BY versions DESC
        """)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def coverage(self) -> dict:
        row = self.con.execute("""
            SELECT COUNT(*),
                   COUNT(DISTINCT entity_key),
                   COUNT(DISTINCT attribute),
                   MIN(observed_at), MAX(observed_at),
                   SUM(CASE WHEN published_at IS NOT NULL THEN 1 ELSE 0 END)
            FROM pit_observations
        """).fetchone()
        return {
            "observations": row[0], "entities": row[1], "attributes": row[2],
            "first_observed": row[3], "last_observed": row[4],
            "with_publication_date": row[5],
        }


def close_price_observations(con, business_date: date, exchange: str = "NSE",
                             evidence_key: str | None = None) -> list[Observation]:
    """Turn one day's closes into PIT observations.

    Prices are already point-in-time by nature, so this is mostly a demonstration that the
    store works end-to-end - and a useful anchor for valuation ratios computed later.
    """
    rows = con.execute("""
        SELECT isin, close_price FROM universe_snapshots
        WHERE business_date = ? AND exchange = ? AND instrument_type = 'STK'
          AND close_price IS NOT NULL
    """, [business_date, exchange]).fetchall()
    published = datetime.combine(business_date, datetime.min.time(), tzinfo=timezone.utc)
    return [
        Observation(
            entity_key=isin, attribute="close_price", value_num=px, unit="INR",
            period_end=business_date, published_at=published,
            source=exchange, evidence_key=evidence_key,
        )
        for isin, px in rows
    ]
