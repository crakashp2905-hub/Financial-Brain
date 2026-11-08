"""Read-only bar source over the project's existing DuckDB and Parquet data (CP-0041, CP-0153).

The source never writes. DuckDB is opened with ``read_only=True`` and Parquet is read through DuckDB's
``read_parquet``. Dates are bound as parameters, never formatted into SQL. The caller gets immutable :class:`Bars`
with bars whose timestamps fall inside the requested half-open session range ``[start, end)``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import duckdb

from financial_brain.patterns.detectors import Bars, InvalidBars

_IDENT_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


class SourceError(RuntimeError):
    """The requested source is missing, not a valid file, or the query returned unusable rows."""


def _safe_identifier(name: str) -> str:
    if not name or not set(name) <= _IDENT_OK:
        raise SourceError(f"unsafe identifier {name!r}")
    return name


class BarSource:
    """Read-only view over one table of OHLC rows.

    ``table`` is a DuckDB table name (or ``read_parquet`` target when ``parquet`` is set). Required columns are
    ``symbol``, ``session`` (ISO date text), ``open``, ``high``, ``low``, ``close``.
    """

    def __init__(self, path: Path, *, table: str = "bars", parquet: bool = False) -> None:
        self._path = Path(path)
        self._table = _safe_identifier(table)
        self._parquet = parquet
        if not self._path.exists():
            raise SourceError(f"source not found: {self._path}")

    def _connect(self) -> duckdb.DuckDBPyConnection:
        if self._parquet:
            con = duckdb.connect(database=":memory:")
            return con
        return duckdb.connect(database=str(self._path), read_only=True)

    def _relation(self) -> str:
        if self._parquet:
            escaped = str(self._path).replace("'", "''")
            return f"read_parquet('{escaped}')"
        return self._table

    def bars(self, symbol: str, start: str, end: str) -> Bars:
        """Return bars for ``symbol`` with ``start <= session < end`` in session order."""
        sql = (
            f"SELECT open, high, low, close FROM {self._relation()} "  # noqa: S608 - identifiers are validated
            "WHERE symbol = ? AND session >= ? AND session < ? ORDER BY session"
        )
        con = self._connect()
        try:
            rows: list[tuple[Any, ...]] = con.execute(sql, [symbol, start, end]).fetchall()
        except duckdb.Error as exc:
            raise SourceError(f"query failed: {exc}") from exc
        finally:
            con.close()
        try:
            return Bars(
                tuple(float(r[0]) for r in rows),
                tuple(float(r[1]) for r in rows),
                tuple(float(r[2]) for r in rows),
                tuple(float(r[3]) for r in rows),
            )
        except InvalidBars as exc:
            raise SourceError(f"invalid bars for {symbol}: {exc}") from exc


def file_sha256(path: Path) -> str:
    """Hash a file's bytes; used to prove a protected source was not modified (CP-0153)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()
