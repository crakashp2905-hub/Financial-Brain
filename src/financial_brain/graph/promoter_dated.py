"""Promoter-group membership that knows when it was true.

[[Result of the promoter graph and pledging tests]] found the strongest and most distinct signal in
this project - sibling momentum and sibling stress, replicating in sample and passing a held-out
cross-section at t of 20 and 24 - and could not claim any of it, because ``promoter_groups`` has no
dates. It is current membership with no ``valid_from``, so the signal applied today's group structure
to 2016.

**A cross-sectional holdout cannot fix that.** Ranks 501-1500 are a different set of names carrying the
same contamination: both samples read the same static table. A holdout tests whether an effect is
specific to a tier; it is silent about a defect in how the signal is built, and that one is in the
construction.

The specific worry is survivorship *in the graph*. Today's membership is the set of relationships that
survived - companies sold out of a group, demerged away, or delisted are simply absent - so sibling
momentum computed over survivors is survivor momentum, and survivors went up.

## Where dated membership comes from

``holder_filings``: 143,058 rows from 2015 to 2026, every one carrying a ``business_date``. Two
companies are linked when the **same filer** appears in both, and the link is dated from the filing
that evidenced it.

Two filters make that a promoter graph rather than an ownership graph:

* **``filer_kind`` must be a person or an organisation, never an institution.** The most prolific
  multi-company filers are LIC (222 companies), SBI Mutual Fund (142) and HDFC Mutual Fund (140).
  Linking two companies because LIC owns both is not a promoter group, it is a statement that both are
  large enough to be owned by LIC - and it would manufacture exactly the size effect that runs through
  every result in this project.
* **the relation must be a pledge or an insider filing.** Pledging is what a promoter does with a
  promoter stake; an insider filing is by a designated or connected person. ``SUBSTANTIAL`` is the
  bulk of the table and includes any holder above the threshold, promoter or not.

That leaves **520 multi-company filers across 499 ISINs and 10,022 filings** - comparable in scale to
the 447-member undated table, and point-in-time by construction.

## What a link means, and how long it lasts

A filing is evidence that a relationship existed **on that date**. It is not evidence that the
relationship began then, nor that it continued. So:

* a link becomes visible at its first filing and **not before** - the signal cannot see a relationship
  the archive has not yet evidenced, which is the correct direction to be wrong in;
* it stays visible for :data:`LINK_TTL` days after the most recent filing evidencing it, and then
  lapses. Without a time-to-live a single 2015 filing would assert the relationship through 2026,
  which is the static table's problem reintroduced one edge at a time.

The lapse is a judgement and it is the main free parameter here, so :func:`membership` takes it and
:func:`sensitivity` reports how much the resulting graph changes across plausible values. A signal
that only works at one TTL is a signal fitted to a TTL.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

#: Filer kinds that can evidence a promoter relationship. Institutions are excluded: linking companies
#: because the same mutual fund holds both is an ownership graph, not a promoter one.
PROMOTER_KINDS = ("person", "organisation")

#: Relations that evidence a promoter relationship rather than any large holding.
PROMOTER_RELATIONS = ("PLEDGE", "INSIDER")

#: How long a filing keeps a link visible, in calendar days. Two years: long enough that an annual
#: pledge-disclosure cadence does not flicker the graph on and off, short enough that a relationship
#: ended in 2017 is not asserted in 2026.
LINK_TTL = 730


class GraphError(ValueError):
    pass


@dataclass(frozen=True)
class Link:
    a: str                 #: lineage
    b: str                 #: lineage, always sorted after ``a``
    filer_key: str
    first_seen: date
    last_seen: date

    def live_on(self, d: date, ttl: int = LINK_TTL) -> bool:
        return self.first_seen <= d <= self.last_seen + timedelta(days=ttl)


def build(con, *, kinds=PROMOTER_KINDS, relations=PROMOTER_RELATIONS) -> int:
    """Materialise dated pairwise links into ``promoter_links``. Returns the row count.

    Pairs rather than groups. A group is a transitive closure over pairs and the closure is only valid
    at a point in time; storing pairs keeps the dating honest and lets a caller close them per session.
    """
    if not kinds or not relations:
        raise GraphError("a promoter graph needs at least one filer kind and one relation")
    con.execute("DROP TABLE IF EXISTS promoter_links")
    con.execute(f"""
        CREATE TABLE promoter_links AS
        WITH f AS (
            SELECT l.lineage, h.filer_key, h.business_date
            FROM holder_filings h
            JOIN security_lineage l ON l.isin = h.isin
            WHERE h.filer_key IS NOT NULL
              AND h.filer_kind IN ({', '.join('?' for _ in kinds)})
              AND h.relation IN ({', '.join('?' for _ in relations)})
        ), pair AS (
            -- a < b fixes one ordering. GREATEST: the link is evidenced only once BOTH have filed.
            SELECT a.lineage AS a, b.lineage AS b, a.filer_key,
                   GREATEST(a.business_date, b.business_date) AS seen
            FROM f a JOIN f b
              ON a.filer_key = b.filer_key AND a.lineage < b.lineage
        )
        SELECT a, b, filer_key,
               MIN(seen) AS first_seen,
               MAX(seen) AS last_seen,
               COUNT(*)  AS filings
        FROM pair GROUP BY a, b, filer_key
    """, list(kinds) + list(relations))
    return con.execute("SELECT COUNT(*) FROM promoter_links").fetchone()[0]


def membership(con, *, ttl: int = LINK_TTL) -> str:
    """A ``(lineage, business_date, sibling)`` view, live only where the archive evidenced it.

    The join is to the *session calendar*, so a sibling appears on a session only if some filing had
    already evidenced the link by then and it has not yet lapsed. That is the whole point: on
    2016-04-01 the graph contains what was knowable on 2016-04-01.
    """
    if ttl < 0:
        raise GraphError(f"ttl {ttl} cannot be negative")
    con.execute("DROP TABLE IF EXISTS promoter_siblings")
    con.execute(f"""
        CREATE TABLE promoter_siblings AS
        WITH cal AS (SELECT DISTINCT business_date FROM features),
        edges AS (
            SELECT a, b, first_seen, last_seen FROM promoter_links
            UNION ALL
            SELECT b, a, first_seen, last_seen FROM promoter_links
        )
        SELECT DISTINCT e.a AS lineage, c.business_date, e.b AS sibling
        FROM edges e JOIN cal c
          ON c.business_date >= e.first_seen
         AND c.business_date <= e.last_seen + INTERVAL {int(ttl)} DAY
    """)
    return "promoter_siblings"


def signals(con) -> str:
    """Sibling-derived features, always **excluding self**, into ``promoter_panel``.

    A group signal that includes the name is that name's own feature with extra steps.
    """
    con.execute("DROP TABLE IF EXISTS promoter_panel")
    con.execute("""
        CREATE TABLE promoter_panel AS
        SELECT s.lineage, s.business_date,
               AVG(f.mom_12_1)        AS group_momentum,
               AVG(f.dist_52w_high)   AS group_stress,
               STDDEV_SAMP(f.ret_20d) AS group_dispersion,
               COUNT(*)               AS siblings
        FROM promoter_siblings s
        JOIN features f ON f.lineage = s.sibling AND f.business_date = s.business_date
        WHERE f.mom_12_1 IS NOT NULL AND f.dist_52w_high IS NOT NULL
        GROUP BY 1, 2
    """)
    return "promoter_panel"


def coverage(con) -> dict:
    """What the dated graph actually covers, against the undated one it replaces."""
    links = con.execute("SELECT COUNT(*) FROM promoter_links").fetchone()[0]
    names = con.execute(
        "SELECT COUNT(DISTINCT lineage) FROM promoter_siblings").fetchone()[0]
    rows = con.execute("SELECT COUNT(*) FROM promoter_panel").fetchone()[0]
    span = con.execute(
        "SELECT MIN(business_date), MAX(business_date) FROM promoter_panel").fetchone()
    per_session = con.execute("""
        SELECT MEDIAN(n) FROM (SELECT business_date, COUNT(*) n
                               FROM promoter_panel GROUP BY 1)""").fetchone()[0]
    static = con.execute(
        "SELECT COUNT(DISTINCT member) FROM promoter_groups").fetchone()[0]
    return {
        "links": links, "lineages": names, "panel_rows": rows,
        "first": span[0], "last": span[1],
        "median_names_per_session": per_session,
        "undated_members_for_comparison": static,
        "why": "the dated graph grows as the archive evidences relationships, so early sessions carry "
               "fewer names than late ones - which is what point-in-time means and what the static "
               "table hid",
    }


def sensitivity(con, ttls=(365, 730, 1095, 1825)) -> list[dict]:
    """How much the graph depends on the lapse parameter.

    The TTL is the one free parameter here, so a signal that only works at one value is fitted to that
    value. Reported so the question cannot be skipped.
    """
    out = []
    for t in ttls:
        membership(con, ttl=t)
        n = con.execute(
            "SELECT COUNT(*), COUNT(DISTINCT lineage) FROM promoter_siblings").fetchone()
        out.append({"ttl_days": t, "sibling_rows": n[0], "lineages": n[1]})
    return out
