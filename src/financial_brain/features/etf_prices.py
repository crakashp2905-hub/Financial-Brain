"""ETF daily price series built from the existing immutable EOD lake.

The output keeps raw exchange OHLCV, adjusted close, trading dates, lake lineage and the
source payload hash together. It never invents a price for a missing exchange row.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date
from typing import Any

from financial_brain.providers.etf_reference import ETFValuationObservation

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


def separate_etf_return_series(
    *,
    expected_sessions: list[date],
    etf_price_close: Mapping[date, float],
    nav_observations: list[ETFValuationObservation],
    underlying_total_return_index: Mapping[date, float],
) -> dict[str, Any]:
    """Return price, NAV and underlying TRI returns as separate, non-filled series.

    A return is emitted only when both endpoints are present on adjacent sourced sessions.
    ETF price and NAV changes do not include distribution reinvestment; the benchmark input
    must be a source-designated total-return index.
    """
    sessions = sorted(set(expected_sessions))
    if any(row.valuation_type != "NAV" for row in nav_observations):
        raise ValueError("NAV return series accepts NAV observations only; iNAV is a separate market estimate")
    if len({row.valuation_date for row in nav_observations}) != len(nav_observations):
        raise ValueError("resolve NAV revisions before computing ETF return series")
    nav_values = {row.valuation_date: float(row.value_per_unit) for row in nav_observations
                  if row.valuation_type == "NAV"}
    sources: dict[str, Mapping[date, float]] = {
        "ETF_PRICE_RETURN": etf_price_close,
        "ETF_NAV_RETURN": nav_values,
        "UNDERLYING_TOTAL_RETURN_INDEX": underlying_total_return_index,
    }
    output: dict[str, Any] = {}
    for name, values in sources.items():
        if any(day not in sessions for day in values):
            raise ValueError(f"{name} contains a date outside sourced expected sessions")
        if any(not math.isfinite(value) or value <= 0 for value in values.values()):
            raise ValueError(f"{name} values must be finite and positive")
        returns: dict[str, float | None] = {}
        prior_value: float | None = None
        for day in sessions:
            current = values.get(day)
            returns[day.isoformat()] = (current / prior_value - 1.0
                                        if current is not None and prior_value is not None else None)
            prior_value = current
        output[name] = returns
    output["methodology"] = {
        "return_type": "simple percentage change",
        "price_and_nav_distributions_reinvested": False,
        "benchmark_input": "source-designated total-return index level",
        "missing_session_policy": "null; do not bridge missing sourced sessions",
    }
    return output


__all__ = ["BUILD_SQL", "build", "separate_etf_return_series"]
