"""Optional local-only read-only UDF API for licensed TradingView charts.

No server starts on import. A caller must supply a database path and bind a
listener deliberately; do not expose licensed Kite data on a public endpoint.
"""
from __future__ import annotations

from pathlib import Path

from .chart_datafeed import ChartDataError, configuration, history, symbol_info


def build_app(database_path: Path):
    try:
        import duckdb
        from fastapi import FastAPI, HTTPException, Query
    except ImportError as exc:
        raise RuntimeError("install the api extra and DuckDB for chart UDF") from exc
    if not Path(database_path).is_file():
        raise FileNotFoundError("chart database is missing")
    app = FastAPI(title="Financial Brain chart archive", docs_url=None, redoc_url=None,
                  openapi_url=None)

    @app.get("/config")
    def config():
        return configuration()

    @app.get("/symbols")
    def symbols(symbol: str):
        try:
            if not symbol.startswith("KITE:") or not symbol[5:].isdigit():
                raise ChartDataError("invalid symbol")
            with duckdb.connect(str(database_path), read_only=True) as con:
                return symbol_info(con, int(symbol[5:]))
        except ChartDataError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/search")
    def search(query: str, type: str = "", exchange: str = "", limit: int = 20):
        if not query or len(query) > 64 or not 1 <= limit <= 20:
            raise HTTPException(status_code=400, detail="invalid search request")
        if exchange not in ("", "KITE_ARCHIVE") or type not in ("", "stock"):
            return []
        with duckdb.connect(str(database_path), read_only=True) as con:
            rows = con.execute("""SELECT instrument_token, MIN(tradingsymbol) AS name
                FROM minute_bars WHERE starts_with(lower(tradingsymbol), lower(?))
                GROUP BY instrument_token HAVING COUNT(DISTINCT tradingsymbol) = 1
                ORDER BY name LIMIT ?""", [query, limit]).fetchall()
        return [{"symbol": f"KITE:{token}", "full_name": f"KITE:{token}",
                 "description": name, "exchange": "KITE_ARCHIVE", "ticker": f"KITE:{token}",
                 "type": "stock"} for token, name in rows]

    @app.get("/history")
    def bars(symbol: str, resolution: str, from_: int = Query(alias="from"), to: int = 0):
        try:
            if not symbol.startswith("KITE:") or not symbol[5:].isdigit():
                raise ChartDataError("invalid symbol")
            with duckdb.connect(str(database_path), read_only=True) as con:
                return history(con, token=int(symbol[5:]), resolution=resolution,
                               start=from_, end=to)
        except ChartDataError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
