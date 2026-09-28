"""Point-in-time adjusted OHLCV, for the models that need more than a close.

A close-only history is enough for a bootstrap and not enough for a model trained on candles. This
module supplies adjusted daily bars, and exists mostly because of two joins that are wrong in ways
that do not raise.

**``eod_prices`` holds more than one bar per name per session.** 9.6M BSE rows and 6.4M NSE rows,
across six series. Joining it to ``adjusted_prices`` on (isin, business_date) alone returns both
exchanges' bars interleaved, and for 2025 onward 44% of the joined rows had a close that disagreed
with the adjusted close - a model fed that history is reading a series that alternates between two
exchanges. ``adjusted_prices`` is built from NSE series EQ (both tables hold exactly 4,921,300 rows),
so that is the filter, stated rather than discovered.

**The raw bars are unadjusted.** ``close_adj = close_raw * factor``, with factors down to 0.00133 in
this database, so a 1:10 split in a raw series reads as a 90% single-session crash. Every price field
in a bar takes the same factor; volume takes its reciprocal, because a split multiplies the share
count by exactly what it divides the price by, and turnover is the invariant that says so.

Nothing here reads a session after ``as_of``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: The exchange and series ``adjusted_prices`` is built from. Anything else is a different series.
EXCHANGE, SERIES = "NSE", "EQ"


@dataclass
class Bar:
    session: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float

    def is_sane(self) -> bool:
        """Whether the bar's own arithmetic holds.

        A bar whose high is below its close, or whose low is above it, is not a bar - it is two
        sources glued together or an adjustment applied to some fields and not others. A model given
        such a bar will learn from it silently.
        """
        return (self.low <= self.open <= self.high
                and self.low <= self.close <= self.high
                and self.low > 0 and self.volume >= 0)


def history(con, isin: str, as_of: date, length: int = 750) -> list[Bar]:
    """Adjusted NSE EQ bars up to and including ``as_of``, oldest first.

    Bars failing :meth:`Bar.is_sane` are dropped rather than repaired: the repair would be a guess
    about which field the adjustment missed, and a guessed candle is worse than a gap.
    """
    rows = con.execute("""
        SELECT e.business_date,
               e.open_price  * a.factor,
               e.high_price  * a.factor,
               e.low_price   * a.factor,
               a.close_adj,
               e.traded_volume / NULLIF(a.factor, 0),
               e.turnover
        FROM eod_prices e
        JOIN adjusted_prices a
          ON a.isin = e.isin AND a.business_date = e.business_date
        WHERE e.isin = ? AND e.business_date <= ?
          AND e.exchange = ? AND e.series = ?
          AND a.close_adj > 0 AND e.low_price > 0
        ORDER BY e.business_date DESC
        LIMIT ?
    """, [isin, as_of, EXCHANGE, SERIES, length]).fetchall()
    out = []
    for r in reversed(rows):
        bar = Bar(session=r[0], open=r[1], high=r[2], low=r[3], close=r[4],
                  volume=r[5] or 0.0, turnover=r[6] or 0.0)
        if bar.is_sane():
            out.append(bar)
    return out


def closes(bars: list[Bar]) -> list[float]:
    return [b.close for b in bars]
