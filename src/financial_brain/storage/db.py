"""DuckDB access layer.

ADR-0001: DuckDB now, PostgreSQL + TimescaleDB later. Curated market data is written as
Parquet (engine-independent); only the small reference/provenance/PIT tables live inside
the database file. The data is therefore portable even though the engine is not.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import duckdb

from ..config import Config, load

SCHEMA = Path(__file__).with_name("schema.sql")


class Database:
    def __init__(self, cfg: Config | None = None, *, read_only: bool = False):
        self.cfg = cfg or load()
        self._read_only = read_only

    @contextmanager
    def connect(self):
        con = duckdb.connect(str(self.cfg.db_path), read_only=self._read_only)
        try:
            yield con
        finally:
            con.close()

    #: Idempotent column additions for databases created by an earlier version.
    MIGRATIONS = [
        "ALTER TABLE ingest_runs ADD COLUMN IF NOT EXISTS rows_rejected BIGINT DEFAULT 0",
        "ALTER TABLE dq_results  ADD COLUMN IF NOT EXISTS scope VARCHAR DEFAULT 'file'",
        "ALTER TABLE corporate_actions ADD COLUMN IF NOT EXISTS confidence VARCHAR",
        "ALTER TABLE corporate_actions ADD COLUMN IF NOT EXISTS derived_factor DOUBLE",
        # 'auto' (fb gaps --auto-review, re-runnable) vs 'manual' (a person's verdict).
        "ALTER TABLE gap_reviews ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR DEFAULT 'manual'",
    ]

    def migrate(self) -> None:
        """Create any missing objects. Safe to run repeatedly."""
        with self.connect() as con:
            con.execute(SCHEMA.read_text(encoding="utf-8"))
            for stmt in self.MIGRATIONS:
                try:
                    con.execute(stmt)
                except duckdb.Error:
                    pass  # column already present on this build
            self._refresh_views(con)

    def _refresh_views(self, con) -> None:
        """Views over the curated Parquet datasets."""
        eod = (self.cfg.curated / "eod_prices").as_posix()
        if any((self.cfg.curated / "eod_prices").rglob("*.parquet")):
            con.execute(
                f"""
                CREATE OR REPLACE VIEW eod_prices AS
                SELECT * FROM read_parquet('{eod}/**/*.parquet', hive_partitioning = true)
                """
            )
        else:
            # Empty but well-typed, so downstream queries and tests work on a cold start.
            con.execute(
                """
                CREATE OR REPLACE VIEW eod_prices AS
                SELECT
                    CAST(NULL AS DATE)    AS business_date,
                    CAST(NULL AS VARCHAR) AS exchange,
                    CAST(NULL AS VARCHAR) AS isin,
                    CAST(NULL AS VARCHAR) AS ticker,
                    CAST(NULL AS VARCHAR) AS series,
                    CAST(NULL AS VARCHAR) AS instrument_type,
                    CAST(NULL AS VARCHAR) AS instrument_id,
                    CAST(NULL AS VARCHAR) AS name,
                    CAST(NULL AS DOUBLE)  AS open_price,
                    CAST(NULL AS DOUBLE)  AS high_price,
                    CAST(NULL AS DOUBLE)  AS low_price,
                    CAST(NULL AS DOUBLE)  AS close_price,
                    CAST(NULL AS DOUBLE)  AS last_price,
                    CAST(NULL AS DOUBLE)  AS prev_close,
                    CAST(NULL AS DOUBLE)  AS settlement_price,
                    CAST(NULL AS BIGINT)  AS traded_volume,
                    CAST(NULL AS DOUBLE)  AS turnover,
                    CAST(NULL AS BIGINT)  AS trades
                WHERE FALSE
                """
            )

    def refresh_views(self) -> None:
        with self.connect() as con:
            self._refresh_views(con)

    # -- convenience ---------------------------------------------------------
    def query(self, sql: str, params: list | None = None):
        with self.connect() as con:
            return con.execute(sql, params or []).fetchall()

    def query_dicts(self, sql: str, params: list | None = None) -> list[dict]:
        with self.connect() as con:
            cur = con.execute(sql, params or [])
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def execute(self, sql: str, params: list | None = None) -> None:
        with self.connect() as con:
            con.execute(sql, params or [])
