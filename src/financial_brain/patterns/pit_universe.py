"""Point-in-time valid universe for pattern research (CP-0156).

A name is in the valid universe on a date only if the equity universe snapshot for that date marks it tradable
and it has stock-futures (FUTSTK) activity on that same date in the F&O bhavcopy table. Both inputs are read as
they were known on the date; nothing is filled from later snapshots. Connections are supplied by the caller and
are expected to be read-only. This module never writes.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb

from financial_brain.universe.snapshot import as_of


def fno_stock_symbols(fo_con: duckdb.DuckDBPyConnection, on_date: date) -> set[str]:
    """Symbols with stock-futures rows on ``on_date`` in the F&O bhavcopy table."""
    rows = fo_con.execute(
        "SELECT DISTINCT symbol FROM fo_bhav WHERE trade_date = ? AND instrument = 'FUTSTK'", [on_date]
    ).fetchall()
    return {str(r[0]) for r in rows}


def valid_universe(
    universe_con: duckdb.DuckDBPyConnection,
    fo_con: duckdb.DuckDBPyConnection,
    on_date: date,
    *,
    exchange: str = "NSE",
    min_turnover: float | None = None,
    require_fno: bool = True,
) -> list[dict[str, Any]]:
    """Equity names tradable on ``on_date`` and, when ``require_fno`` is set, with stock-futures activity that day.

    Each row carries ``fno_member``. A name without F&O activity is kept and marked ``False`` unless
    ``require_fno`` is set, so the caller can see what the filter removed.
    """
    members = fno_stock_symbols(fo_con, on_date)
    rows = as_of(universe_con, on_date, exchange=exchange, instrument_type="STK", min_turnover=min_turnover)
    out: list[dict[str, Any]] = []
    for row in rows:
        flagged = dict(row, fno_member=str(row.get("ticker")) in members)
        if require_fno and not flagged["fno_member"]:
            continue
        out.append(flagged)
    return out
