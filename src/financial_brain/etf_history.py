"""ETF-specific view over the existing hashed EOD and adjustment stores."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


class ETFHistoryUnavailable(ValueError):
    """Raised when the ETF identity or adjusted source bars are not established."""


@dataclass(frozen=True)
class ETFPriceBar:
    trade_date: date
    isin: str
    ticker: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    turnover: float | None
    adjustment_factor: float
    evidence_key: str


def history(con, isin: str, as_of: date, *, reference_as_of: date | None = None,
            limit: int | None = None) -> list[ETFPriceBar]:
    """Return NSE EQ ETF OHLCV with corporate-action-adjusted prices and source keys.

    The ETF identity must have an exchange reference observation available on or before
    ``reference_as_of`` (defaults to ``as_of``). The generic adjusted price table is joined
    back to EOD source rows so each bar retains its immutable lake key. No missing session
    is synthesized.
    """
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    ref_as_of = reference_as_of or as_of
    ref = con.execute(
        """SELECT listing_date, status FROM etf_reference_observations
           WHERE isin = ? AND exchange = 'NSE' AND observed_on <= ?
           ORDER BY observed_on DESC LIMIT 1""",
        [isin, ref_as_of],
    ).fetchone()
    if not ref or ref[1] != "LISTED":
        raise ETFHistoryUnavailable(f"no point-in-time NSE ETF reference for {isin} on {as_of}")
    listing_date = ref[0]
    if listing_date is not None and listing_date > as_of:
        raise ETFHistoryUnavailable(f"ETF {isin} was not yet listed on {as_of}")

    rows = con.execute(
        """SELECT e.business_date, e.isin, e.ticker,
                  e.open_price * a.factor, e.high_price * a.factor,
                  e.low_price * a.factor, a.close_adj,
                  CAST(e.traded_volume / NULLIF(a.factor, 0) AS BIGINT),
                  e.turnover, a.factor, e.evidence_key
           FROM eod_prices e
           JOIN adjusted_prices a ON a.isin = e.isin AND a.business_date = e.business_date
           WHERE e.exchange = 'NSE' AND e.series = 'EQ' AND e.isin = ?
             AND e.business_date <= ?
           ORDER BY e.business_date DESC""",
        [isin, as_of],
    ).fetchall()
    if not rows:
        raise ETFHistoryUnavailable(f"no adjusted NSE EOD bars available for ETF {isin}")
    if limit is not None:
        rows = rows[:limit]
    return [ETFPriceBar(*row) for row in reversed(rows)]
