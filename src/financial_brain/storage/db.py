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
        # The IC series per trial, so the effective number of independent tests can be measured
        # rather than bounded to [families, trials]. See evaluation/families.py.
        "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS ic_series DOUBLE[]",
        "ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS ic_dates DATE[]",
        "ALTER TABLE forecast_distributions ADD COLUMN IF NOT EXISTS crps DOUBLE",
        "ALTER TABLE forecast_distributions ADD COLUMN IF NOT EXISTS pit DOUBLE",
        "ALTER TABLE opportunity_memory ADD COLUMN IF NOT EXISTS unresolvable BOOLEAN",
        "ALTER TABLE dq_results  ADD COLUMN IF NOT EXISTS scope VARCHAR DEFAULT 'file'",
        "ALTER TABLE corporate_actions ADD COLUMN IF NOT EXISTS confidence VARCHAR",
        "ALTER TABLE corporate_actions ADD COLUMN IF NOT EXISTS derived_factor DOUBLE",
        # 'auto' (fb gaps --auto-review, re-runnable) vs 'manual' (a person's verdict).
        "ALTER TABLE gap_reviews ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR DEFAULT 'manual'",
        # ADR-0002 router: which task, tier and model; latency and money per call.
        "ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS task VARCHAR",
        "ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS tier INTEGER",
        "ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS latency_ms INTEGER",
        "ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS cost_usd DOUBLE",
        # Which text the tone model actually read: the filing, or the news headline the
        # filing quotes (events/newsref.py).
        "ALTER TABLE announcement_tone ADD COLUMN IF NOT EXISTS text_source VARCHAR",
        "ALTER TABLE mf_paper_lots ADD COLUMN IF NOT EXISTS plan VARCHAR DEFAULT 'UNKNOWN'",
        "ALTER TABLE mf_paper_lots ADD COLUMN IF NOT EXISTS option VARCHAR DEFAULT 'UNKNOWN'",
        "ALTER TABLE mf_paper_lots ADD COLUMN IF NOT EXISTS lock_in_until DATE",
        # Folio ownership was not stored on legacy paper lots. Leave historical rows
        # NULL; explicit reconciliation is required before folio-scoped transactions.
        "ALTER TABLE mf_paper_lots ADD COLUMN IF NOT EXISTS folio_id VARCHAR",
        "ALTER TABLE mf_paper_events ADD COLUMN IF NOT EXISTS folio_id VARCHAR",
        "ALTER TABLE mf_paper_redemption_settlements ADD COLUMN IF NOT EXISTS folio_id VARCHAR",
        "ALTER TABLE mf_paper_events ADD COLUMN IF NOT EXISTS plan VARCHAR DEFAULT 'UNKNOWN'",
        "ALTER TABLE mf_paper_events ADD COLUMN IF NOT EXISTS option VARCHAR DEFAULT 'UNKNOWN'",
        "ALTER TABLE mf_scheme_commercial_terms ADD COLUMN IF NOT EXISTS minimum_redemption_inr VARCHAR",
        "ALTER TABLE mf_scheme_commercial_terms ADD COLUMN IF NOT EXISTS charge_schedule_json VARCHAR",
        "ALTER TABLE mf_paper_events ADD COLUMN IF NOT EXISTS other_charges_inr DECIMAL(38,18) DEFAULT 0",
        "ALTER TABLE mf_paper_redemption_settlements ADD COLUMN IF NOT EXISTS other_charges_inr DECIMAL(38,18) DEFAULT 0",
        # Older MF NAV snapshots have observation time but not a separately recorded
        # local persistence time. Keep those values NULL rather than inventing history.
        "ALTER TABLE mf_nav_observations ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ",
        # Persist the specific effective paper-use grant that authorized each observed
        # purchase-payment milestone so its event hash can be independently reproduced.
        "ALTER TABLE mf_paper_purchase_payment_events ADD COLUMN IF NOT EXISTS permission_evidence_json VARCHAR DEFAULT '[]'",
        "ALTER TABLE mf_paper_redemption_settlements ADD COLUMN IF NOT EXISTS order_id VARCHAR",
        "ALTER TABLE mf_paper_redemption_settlements ADD COLUMN IF NOT EXISTS eligibility_sha256 VARCHAR",
        "ALTER TABLE mf_paper_redemption_settlements ADD COLUMN IF NOT EXISTS eligibility_evidence_json VARCHAR",
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
            self._migrate_mf_ledger_account_codes(con)
            self._migrate_mf_purchase_eligibility_cancel(con)
            self._migrate_mf_systematic_event_cancellation(con)
            self._refresh_views(con)

    @staticmethod
    def _migrate_mf_ledger_account_codes(con) -> None:
        """Add the SIP payment-clearing account to older MF paper journals."""
        constraints = con.execute("""SELECT constraint_text FROM duckdb_constraints()
            WHERE table_name='mf_paper_ledger_entries' AND constraint_type='CHECK'""").fetchall()
        if any("SIP_PAYMENT_PENDING" in str(row[0]) for row in constraints):
            return
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("""CREATE TABLE mf_paper_ledger_entries_upgrade (
                account_id VARCHAR NOT NULL,
                transaction_id VARCHAR NOT NULL,
                line_no INTEGER NOT NULL CHECK (line_no > 0),
                effective_at TIMESTAMPTZ NOT NULL,
                recorded_at TIMESTAMPTZ NOT NULL,
                account_code VARCHAR NOT NULL CHECK (account_code IN (
                    'CASH', 'MF_INVESTMENTS', 'REDEMPTION_RECEIVABLE',
                    'OWNER_EQUITY', 'OWNER_WITHDRAWALS', 'EXIT_LOAD_EXPENSE',
                    'OTHER_CHARGES_EXPENSE', 'IDCW_INCOME', 'REALIZED_GAIN',
                    'REALIZED_LOSS', 'SIP_PAYMENT_PENDING')),
                debit_inr DECIMAL(38,18) NOT NULL CHECK (debit_inr >= 0),
                credit_inr DECIMAL(38,18) NOT NULL CHECK (credit_inr >= 0),
                basis VARCHAR NOT NULL CHECK (basis IN (
                    'SIMULATED_ASSUMPTION', 'OBSERVED_EVIDENCE')),
                source_id VARCHAR, source_ref VARCHAR, source_sha256 VARCHAR,
                event_sha256 VARCHAR NOT NULL, evidence_json VARCHAR NOT NULL,
                PRIMARY KEY (account_id, transaction_id, line_no),
                CHECK ((debit_inr > 0 AND credit_inr = 0)
                    OR (credit_inr > 0 AND debit_inr = 0)),
                CHECK ((basis='OBSERVED_EVIDENCE' AND source_id IS NOT NULL
                    AND source_ref IS NOT NULL AND source_sha256 IS NOT NULL)
                    OR (basis='SIMULATED_ASSUMPTION' AND source_id IS NULL
                    AND source_ref IS NULL AND source_sha256 IS NULL))
            )""")
            con.execute("""INSERT INTO mf_paper_ledger_entries_upgrade
                SELECT * FROM mf_paper_ledger_entries""")
            con.execute("DROP TABLE mf_paper_ledger_entries")
            con.execute("""ALTER TABLE mf_paper_ledger_entries_upgrade
                RENAME TO mf_paper_ledger_entries""")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    @staticmethod
    def _migrate_mf_purchase_eligibility_cancel(con) -> None:
        """Allow a reserved, unallotted MF purchase to be durably cancelled."""
        constraints = con.execute("""SELECT constraint_text FROM duckdb_constraints()
            WHERE table_name='mf_paper_purchase_eligibility'
            AND constraint_type='CHECK'""").fetchall()
        if any("CANCELLED" in str(row[0]) for row in constraints):
            return
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("""CREATE TABLE mf_paper_purchase_eligibility_upgrade (
                order_id VARCHAR PRIMARY KEY,
                state VARCHAR NOT NULL CHECK (state IN ('NAV_PENDING', 'ALLOTTED', 'CANCELLED')),
                eligible_nav_date DATE NOT NULL,
                transaction_at TIMESTAMPTZ NOT NULL,
                decision_at TIMESTAMPTZ NOT NULL,
                evidence_json VARCHAR NOT NULL,
                eligibility_sha256 VARCHAR NOT NULL,
                nav_observation_id VARCHAR,
                nav_per_unit DECIMAL(38,18),
                units_allotted DECIMAL(38,18),
                allotment_sha256 VARCHAR,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                CHECK ((state='NAV_PENDING' AND nav_observation_id IS NULL
                    AND nav_per_unit IS NULL AND units_allotted IS NULL
                    AND allotment_sha256 IS NULL)
                    OR (state='ALLOTTED' AND nav_observation_id IS NOT NULL
                    AND nav_per_unit > 0 AND units_allotted > 0
                    AND allotment_sha256 IS NOT NULL)
                    OR (state='CANCELLED' AND nav_observation_id IS NULL
                    AND nav_per_unit IS NULL AND units_allotted IS NULL
                    AND allotment_sha256 IS NULL))
            )""")
            con.execute("""INSERT INTO mf_paper_purchase_eligibility_upgrade
                SELECT * FROM mf_paper_purchase_eligibility""")
            con.execute("DROP TABLE mf_paper_purchase_eligibility")
            con.execute("""ALTER TABLE mf_paper_purchase_eligibility_upgrade
                RENAME TO mf_paper_purchase_eligibility""")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    @staticmethod
    def _migrate_mf_systematic_event_cancellation(con) -> None:
        """Permit an immutable terminal cancellation event on older SWP/STP books."""
        constraints = con.execute("""SELECT constraint_text FROM duckdb_constraints()
            WHERE table_name='mf_paper_systematic_events'
            AND constraint_type='CHECK'""").fetchall()
        if any("PURCHASE_CANCELLED" in str(row[0]) for row in constraints):
            return
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("""CREATE TABLE mf_paper_systematic_events_upgrade (
                instruction_id VARCHAR NOT NULL,
                sequence INTEGER NOT NULL CHECK (sequence > 0),
                event_id VARCHAR NOT NULL,
                kind VARCHAR NOT NULL CHECK (kind IN (
                    'REDEMPTION_REQUESTED', 'VALUE_CONFIRMED', 'PAYOUT_AVAILABLE',
                    'PURCHASE_FUNDS_APPLIED', 'ALLOTMENT_CONFIRMED',
                    'PURCHASE_CANCELLED', 'MISSED')),
                effective_at TIMESTAMPTZ NOT NULL,
                recorded_at TIMESTAMPTZ NOT NULL,
                basis VARCHAR NOT NULL CHECK (basis IN (
                    'SIMULATED_ASSUMPTION', 'OBSERVED_EVIDENCE')),
                amount_inr DECIMAL(38,18), gross_inr DECIMAL(38,18),
                exit_load_inr DECIMAL(38,18), other_charges_inr DECIMAL(38,18),
                nav_per_unit DECIMAL(38,18), units DECIMAL(38,18), reason VARCHAR,
                source_id VARCHAR, source_ref VARCHAR, source_sha256 VARCHAR,
                event_sha256 VARCHAR NOT NULL, evidence_json VARCHAR NOT NULL,
                PRIMARY KEY (instruction_id, sequence),
                UNIQUE (instruction_id, event_id),
                UNIQUE (instruction_id, event_sha256)
            )""")
            con.execute("""INSERT INTO mf_paper_systematic_events_upgrade
                SELECT * FROM mf_paper_systematic_events""")
            con.execute("DROP TABLE mf_paper_systematic_events")
            con.execute("""ALTER TABLE mf_paper_systematic_events_upgrade
                RENAME TO mf_paper_systematic_events""")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    def _refresh_views(self, con) -> None:
        """Views over the curated Parquet datasets, plus derived views."""
        from ..indices.lineage import CANONICAL_VIEW
        con.execute(CANONICAL_VIEW)
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
