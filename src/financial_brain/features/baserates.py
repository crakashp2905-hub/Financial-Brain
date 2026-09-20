"""How unusual is this, for this company and against the market? (research depth)

A fact without a base rate is not analysis. "The auditor resigned" reads the same whether
it is the company's first auditor change in a decade or its third in three years, and
whether 5% or 0.1% of listed companies did the same thing last year. The archive already
holds eleven years of filings for every listed company, so both numbers are a query.

Two rates are computed, both as of a date so nothing leaks from the future:

* **the company's own history** - how many times this has happened here, over 1 and 3
  years, and when it last happened;
* **the market** - what share of companies filed the same event type in the last year,
  and the median count among those that did.

Peers are the whole listed market, not an industry: our reference data has no reliable
sector mapping yet, and a market-wide rate that is honest about what it compares beats a
sector rate built on a guess. The claim says which comparison it made.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class BaseRate:
    isin: str
    event_type: str
    as_of: date
    company_12m: int
    company_36m: int
    last_seen: date | None
    market_share_12m: float      # share of companies with >= 1 such filing in 12m
    market_companies: int        # how many companies filed it
    universe: int                # companies with any filing in the window
    peer_p90_36m: float = 0.0    # 90th percentile of 3-year counts among its filers
    peer_group: str = "all listed companies"

    def unusual(self) -> bool:
        """Unusual *for a company that does this at all*.

        Rarity of the event type is not enough: NRB Bearing files 47 shareholding
        disclosures in three years and that is simply what an actively traded promoter
        group produces. What marks a company out is filing far more than its fellow
        filers do over the same span, so its three years are measured against the 90th
        percentile of theirs - the same window, or a company with an old cluster of
        auditor changes would stop looking unusual the moment a year passed quietly.
        """
        return (self.company_36m >= 2 and self.market_share_12m <= 0.10
                and self.company_36m >= max(2.0, self.peer_p90_36m))

    def describe(self) -> str:
        """One sentence a reader can act on."""
        times = {1: "once", 2: "twice"}.get(self.company_36m, f"{self.company_36m} times")
        share = (f"{self.market_share_12m:.1%} of {self.universe:,} companies filed one "
                 f"in the last year")
        if self.company_36m <= 1:
            return f"first in three years here; {share}"
        last = f", last on {self.last_seen}" if self.last_seen else ""
        peers = (f"; the busiest tenth of filers managed {self.peer_p90_36m:.0f} "
                 f"in that time" if self.peer_p90_36m else "")
        return f"{times} here in three years{last}; {share}{peers}"


def _count(con, isin: str, event_type: str, start: date, end: date) -> int:
    return con.execute("""SELECT COUNT(*) FROM announcements
                          WHERE isin = ? AND event_type = ? AND business_date > ?
                            AND business_date <= ?""",
                       [isin, event_type, start, end]).fetchone()[0]


def rate(con, isin: str, event_type: str, as_of: date) -> BaseRate:
    """Everything needed to say how unusual this filing is, as of ``as_of``."""
    year_ago, three_years = as_of - timedelta(days=365), as_of - timedelta(days=3 * 365)
    last = con.execute("""SELECT MAX(business_date) FROM announcements
                          WHERE isin = ? AND event_type = ? AND business_date < ?""",
                       [isin, event_type, as_of]).fetchone()[0]
    filed, universe = con.execute("""
        SELECT COUNT(DISTINCT CASE WHEN event_type = ? THEN isin END),
               COUNT(DISTINCT isin)
        FROM announcements
        WHERE business_date > ? AND business_date <= ? AND isin IS NOT NULL""",
        [event_type, year_ago, as_of]).fetchone()
    p90 = con.execute("""
        SELECT COALESCE(QUANTILE_CONT(n, 0.9), 0) FROM (
            SELECT COUNT(*) AS n FROM announcements
            WHERE event_type = ? AND business_date > ? AND business_date <= ?
              AND isin IS NOT NULL GROUP BY isin)""",
        [event_type, three_years, as_of]).fetchone()[0]
    return BaseRate(
        peer_p90_36m=float(p90 or 0),
        isin=isin, event_type=event_type, as_of=as_of,
        company_12m=_count(con, isin, event_type, year_ago, as_of),
        company_36m=_count(con, isin, event_type, three_years, as_of),
        last_seen=last,
        market_share_12m=(filed / universe) if universe else 0.0,
        market_companies=filed or 0, universe=universe or 0)


def repeated(con, isin: str, as_of: date, event_types: tuple[str, ...],
             *, min_count: int = 2) -> list[BaseRate]:
    """The event types this company has filed repeatedly - the pattern a single filing
    hides. Used for red flags, where the second occurrence means more than the first."""
    out = []
    for event_type in event_types:
        r = rate(con, isin, event_type, as_of)
        if r.company_36m >= min_count:
            out.append(r)
    return sorted(out, key=lambda r: -r.company_36m)
