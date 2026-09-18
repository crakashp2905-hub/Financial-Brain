"""ISIN resolution for BSE files published before ISINs were included (pre 8 Dec 2016).

BSE's archive before 2016-12-08 is ``EQddmmyy_CSV.ZIP``: scrip code, name, group and
prices - no ISIN. Everything downstream is ISIN-keyed, so each row needs one.

The obvious mapping - scrip code to whatever ISIN it carries later - is unsafe. A scrip
code survives the ISIN change that a face-value split or scheme restructuring causes:
884 BSE codes in the 2016-2026 data have carried more than one ISIN (533385 moved from
INF247L01031 to INF247L01AP3 in June 2021). Mapping blindly would stamp a post-split
ISIN onto pre-split prices and manufacture exactly the corporate-action errors this
history exists to catch.

So every mapping is *corroborated per day*:

1. candidate = the earliest ISIN the scrip code was ever seen with (nearest in time to
   the pre-2016 period);
2. the row is kept only if NSE traded that same ISIN on the same day at a close within
   ``TOLERANCE`` of BSE's close.

A wrong candidate (a later post-split ISIN) did not trade on NSE that day, so it fails
step 2 and the row is dropped rather than mislabelled. The cost is that BSE-only scrips
cannot be recovered for this period; they are counted and reported, never guessed. The
value that *is* recovered - cross-exchange confirmation for 2015-16 corporate actions -
is the reason BSE history matters here at all.
"""
from __future__ import annotations

from datetime import date

#: Cross-exchange close tolerance. NSE and BSE closes for the same security are
#: normally within a fraction of a percent; 3% allows for thin BSE trading and
#: closing-auction differences while being far tighter than any split ratio.
TOLERANCE = 0.03


def needs_resolution(con, table: str) -> bool:
    """True when no row in the payload carries an ISIN (the pre-2016 BSE format)."""
    with_isin = con.execute(
        f"SELECT COUNT(*) FROM {table} WHERE TRIM(COALESCE(ISIN, '')) <> ''").fetchone()[0]
    total = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return total > 0 and with_isin == 0


def resolve(con, table: str, business_date: date) -> tuple[int, int]:
    """Fill ISIN in ``table`` where NSE corroborates it; delete the rest.

    Returns ``(resolved, unresolved)``. Requires NSE for ``business_date`` to be loaded.
    """
    total = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    con.execute("""
        CREATE OR REPLACE TEMP TABLE _bse_code_isin AS
        SELECT instrument_id, arg_min(isin, first_seen) AS isin
        FROM security_listings
        WHERE exchange = 'BSE' AND instrument_id IS NOT NULL AND instrument_id <> ''
        GROUP BY instrument_id
    """)
    con.execute(f"""
        UPDATE {table} SET ISIN = m.isin
        FROM _bse_code_isin m
        WHERE TRIM({table}.FinInstrmId) = m.instrument_id
          AND TRY_CAST({table}.ClsPric AS DOUBLE) > 0
          AND EXISTS (
                SELECT 1 FROM universe_snapshots n
                WHERE n.business_date = ? AND n.exchange = 'NSE'
                  AND n.isin = m.isin AND n.close_price > 0
                  AND ABS(TRY_CAST({table}.ClsPric AS DOUBLE) / n.close_price - 1) <= ?)
    """, [business_date, TOLERANCE])
    con.execute(f"DELETE FROM {table} WHERE TRIM(COALESCE(ISIN, '')) = ''")

    resolved = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return resolved, total - resolved
