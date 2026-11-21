"""ETF daily price series built from the existing immutable EOD lake.

The output keeps raw exchange OHLCV, adjusted close, trading dates, lake lineage and the
source payload hash together. It never invents a price for a missing exchange row.
"""
from __future__ import annotations


BUILD_SQL = """
CREATE OR REPLACE TABLE etf_daily_prices AS
WITH source_rows AS (
    SELECT e.business_date, e.exchange, e.isin, e.ticker, e.series, e.instrument_id,
           e.open_price, e.high_price, e.low_price, e.close_price, e.traded_volume,
           e.turnover, e.evidence_key, m.sha256 AS source_sha256
    FROM eod_prices e
    JOIN lake_manifest m ON m.key = e.evidence_key
    JOIN universe_snapshots u
      ON u.business_date = e.business_date
     AND u.exchange = e.exchange
     AND u.isin = e.isin
     AND u.series = COALESCE(NULLIF(TRIM(e.series), ''), '-')
     AND u.instrument_type = 'ETF'
     AND u.tradable
    WHERE UPPER(TRIM(e.instrument_type)) = 'ETF'
      AND e.business_date IS NOT NULL
      AND e.isin IS NOT NULL
      AND LENGTH(m.sha256) = 64
      AND REGEXP_FULL_MATCH(m.sha256, '[0-9a-f]{64}')
), priced AS (
    SELECT s.*,
           COALESCE((
               SELECT EXP(SUM(LN(a.price_factor)))
               FROM adjustment_factors a
               WHERE a.isin = s.isin
                 AND a.effective_from > s.business_date
                 AND a.price_factor > 0
           ), 1.0) AS adjustment_factor
    FROM source_rows s
)
SELECT business_date, exchange, isin, ticker, series, instrument_id,
       open_price, high_price, low_price, close_price AS close_raw,
       close_price * adjustment_factor AS close_adjusted,
       adjustment_factor, traded_volume, turnover, evidence_key, source_sha256
FROM priced
"""


def build(con) -> dict[str, int]:
    """Rebuild the ETF daily mart from the current curated EOD and raw-lake manifest."""
    con.execute(BUILD_SQL)
    row_count, source_count = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT source_sha256) FROM etf_daily_prices"
    ).fetchone()
    return {"rows": int(row_count), "source_payloads": int(source_count)}


__all__ = ["BUILD_SQL", "build"]
