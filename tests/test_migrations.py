"""Schema migrations: a fresh database, a re-run, and a database from an earlier version all end at the same schema."""
from decimal import Decimal

import duckdb

from financial_brain.config import Config
from financial_brain.storage.db import Database, MigrationDriftError


def _schema(path):
    con = duckdb.connect(str(path), read_only=True)
    try:
        return sorted(con.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                                  "WHERE table_schema = 'main'").fetchall())
    finally:
        con.close()


def test_migrate_twice_is_a_no_op_and_creates_every_table(tmp_path):
    cfg = Config(data_root=tmp_path).ensure()
    Database(cfg).migrate()
    first = _schema(cfg.db_path)
    Database(cfg).migrate()
    assert _schema(cfg.db_path) == first
    tables = {t for t, _, _ in first}
    assert {"financial_results", "xbrl_facts", "delivery", "insider_disclosures"} <= tables


def test_additive_and_structural_migrations_record_stable_checksums(tmp_path):
    cfg = Config(data_root=tmp_path).ensure()
    db = Database(cfg)
    db.migrate()
    with db.connect() as con:
        rows = con.execute(
            "SELECT migration_id, checksum_sha256 FROM schema_migrations ORDER BY migration_id"
        ).fetchall()
    assert len(rows) == len(Database.MIGRATIONS) + 3
    assert len({row[0] for row in rows}) == len(rows)
    assert all(len(row[1]) == 64 and set(row[1]) <= set("0123456789abcdef") for row in rows)
    before = rows
    db.migrate()
    with db.connect() as con:
        after = con.execute(
            "SELECT migration_id, checksum_sha256 FROM schema_migrations ORDER BY migration_id"
        ).fetchall()
    assert after == before


def test_changing_an_applied_migration_definition_fails_closed(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path).ensure()
    Database(cfg).migrate()
    changed = list(Database.MIGRATIONS)
    changed[0] = changed[0].replace("DEFAULT 0", "DEFAULT 1")
    assert changed[0] != Database.MIGRATIONS[0]
    monkeypatch.setattr(Database, "MIGRATIONS", changed)

    try:
        Database(cfg).migrate()
    except MigrationDriftError as exc:
        assert "column:ingest_runs.rows_rejected" in str(exc)
    else:
        raise AssertionError("a changed applied migration must fail closed")


def test_changing_an_applied_structural_transform_fails_closed(tmp_path):
    cfg = Config(data_root=tmp_path).ensure()
    Database(cfg).migrate()
    with Database(cfg).connect() as con:
        con.execute(
            """UPDATE schema_migrations SET checksum_sha256 = ?
               WHERE migration_id = 'mf-ledger-account-codes-v1'""",
            ["f" * 64],
        )

    try:
        Database(cfg).migrate()
    except MigrationDriftError as exc:
        assert "mf-ledger-account-codes-v1" in str(exc)
    else:
        raise AssertionError("a changed structural transformation must fail closed")


def test_an_older_database_gains_the_added_columns(tmp_path):
    cfg = Config(data_root=tmp_path).ensure()
    Database(cfg).migrate()
    fresh = _schema(cfg.db_path)
    con = duckdb.connect(str(cfg.db_path))
    con.execute("ALTER TABLE llm_calls DROP COLUMN cost_usd")
    con.execute("ALTER TABLE announcement_tone DROP COLUMN text_source")
    con.execute("ALTER TABLE mf_scheme_commercial_terms DROP COLUMN minimum_redemption_inr")
    con.execute("ALTER TABLE mf_paper_redemption_settlements DROP COLUMN order_id")
    con.execute("ALTER TABLE mf_paper_redemption_settlements DROP COLUMN eligibility_sha256")
    con.execute("ALTER TABLE mf_paper_redemption_settlements DROP COLUMN eligibility_evidence_json")
    con.execute("ALTER TABLE mf_paper_events DROP COLUMN folio_id")
    con.close()
    Database(cfg).migrate()
    assert _schema(cfg.db_path) == fresh


def test_old_mf_journal_accepts_sip_reserve_without_losing_rows(tmp_path):
    cfg = Config(data_root=tmp_path).ensure()
    Database(cfg).migrate()
    con = duckdb.connect(str(cfg.db_path))
    con.execute("DROP TABLE mf_paper_ledger_entries")
    con.execute("""CREATE TABLE mf_paper_ledger_entries (
        account_id VARCHAR NOT NULL, transaction_id VARCHAR NOT NULL,
        line_no INTEGER NOT NULL CHECK (line_no > 0),
        effective_at TIMESTAMPTZ NOT NULL, recorded_at TIMESTAMPTZ NOT NULL,
        account_code VARCHAR NOT NULL CHECK (account_code IN (
            'CASH', 'MF_INVESTMENTS', 'REDEMPTION_RECEIVABLE',
            'OWNER_EQUITY', 'OWNER_WITHDRAWALS', 'EXIT_LOAD_EXPENSE',
            'OTHER_CHARGES_EXPENSE', 'IDCW_INCOME', 'REALIZED_GAIN', 'REALIZED_LOSS')),
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
    con.execute("""INSERT INTO mf_paper_ledger_entries VALUES
        ('paper', 'legacy-opening', 1, TIMESTAMPTZ '2026-01-01 00:00:00+00',
         TIMESTAMPTZ '2026-01-01 00:00:00+00', 'CASH', 100, 0,
         'SIMULATED_ASSUMPTION', NULL, NULL, NULL, 'hash', '{}')""")
    con.close()
    Database(cfg).migrate()
    con = duckdb.connect(str(cfg.db_path))
    assert con.execute("""SELECT account_code, debit_inr FROM mf_paper_ledger_entries
        WHERE transaction_id='legacy-opening'""").fetchone() == ("CASH", Decimal("100"))
    assert con.execute("""SELECT folio_id, instrument_id, debit_units, credit_units
        FROM mf_paper_ledger_entries WHERE transaction_id='legacy-opening'""").fetchone() == (
            None, None, Decimal(0), Decimal(0),
        )
    con.execute("""INSERT INTO mf_paper_ledger_entries (
        account_id, transaction_id, line_no, effective_at, recorded_at, account_code,
        debit_inr, credit_inr, basis, source_id, source_ref, source_sha256,
        event_sha256, evidence_json)
        VALUES
        ('paper', 'sip-reserve', 1, TIMESTAMPTZ '2026-01-02 00:00:00+00',
         TIMESTAMPTZ '2026-01-02 00:00:00+00', 'SIP_PAYMENT_PENDING', 10, 0,
         'SIMULATED_ASSUMPTION', NULL, NULL, NULL, 'hash-2', '{}')""")
    assert con.execute("""SELECT COUNT(*) FROM mf_paper_ledger_entries
        WHERE account_code='SIP_PAYMENT_PENDING'""").fetchone()[0] == 1
    con.close()


def test_legacy_lot_table_migration_adds_unassigned_folio_without_guessing(tmp_path):
    cfg = Config(data_root=tmp_path).ensure()
    con = duckdb.connect(str(cfg.db_path))
    con.execute("""CREATE TABLE mf_paper_lots (
        account_id VARCHAR NOT NULL, lot_id VARCHAR NOT NULL,
        scheme_code VARCHAR NOT NULL, plan VARCHAR NOT NULL, option VARCHAR NOT NULL,
        isin VARCHAR NOT NULL, allotted_on DATE NOT NULL, lock_in_until DATE,
        units_original DECIMAL(38,18) NOT NULL, units_remaining DECIMAL(38,18) NOT NULL,
        purchase_nav DECIMAL(38,18) NOT NULL, exit_loads_json VARCHAR,
        exit_loads_sha256 VARCHAR, subscription_event_id VARCHAR NOT NULL,
        created_at TIMESTAMPTZ NOT NULL, PRIMARY KEY(account_id, lot_id),
        UNIQUE(account_id, subscription_event_id))""")
    con.execute("""INSERT INTO mf_paper_lots VALUES (
        'paper', 'old-lot', '123456', 'DIRECT', 'GROWTH', 'INF123456789',
        DATE '2026-01-01', NULL, 2, 2, 10, NULL, NULL,
        'old-subscription', TIMESTAMPTZ '2026-01-01 00:00:00+00')""")
    con.close()

    Database(cfg).migrate()

    con = duckdb.connect(str(cfg.db_path), read_only=True)
    assert "folio_id" in {
        row[1] for row in con.execute("PRAGMA table_info('mf_paper_lots')").fetchall()
    }
    assert con.execute("SELECT lot_id, folio_id FROM mf_paper_lots").fetchone() == (
        "old-lot", None,
    )
    con.close()


def test_older_systematic_purchase_tables_gain_cancellation_states_without_losing_rows(tmp_path):
    cfg = Config(data_root=tmp_path).ensure()
    Database(cfg).migrate()
    con = duckdb.connect(str(cfg.db_path))
    con.execute("DROP TABLE mf_paper_purchase_eligibility")
    con.execute("""CREATE TABLE mf_paper_purchase_eligibility (
        order_id VARCHAR PRIMARY KEY,
        state VARCHAR NOT NULL CHECK (state IN ('NAV_PENDING', 'ALLOTTED')),
        eligible_nav_date DATE NOT NULL, transaction_at TIMESTAMPTZ NOT NULL,
        decision_at TIMESTAMPTZ NOT NULL, evidence_json VARCHAR NOT NULL,
        eligibility_sha256 VARCHAR NOT NULL, nav_observation_id VARCHAR,
        nav_per_unit DECIMAL(38,18), units_allotted DECIMAL(38,18),
        allotment_sha256 VARCHAR, created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CHECK ((state='NAV_PENDING' AND nav_observation_id IS NULL
            AND nav_per_unit IS NULL AND units_allotted IS NULL AND allotment_sha256 IS NULL)
            OR (state='ALLOTTED' AND nav_observation_id IS NOT NULL
            AND nav_per_unit > 0 AND units_allotted > 0 AND allotment_sha256 IS NOT NULL))
    )""")
    con.execute("""INSERT INTO mf_paper_purchase_eligibility VALUES (
        'old-purchase', 'NAV_PENDING', DATE '2026-11-23',
        TIMESTAMPTZ '2026-11-23 00:00:00+00', TIMESTAMPTZ '2026-11-23 00:00:00+00',
        '{}', 'eligibility-hash', NULL, NULL, NULL, NULL,
        TIMESTAMPTZ '2026-11-23 00:00:00+00', TIMESTAMPTZ '2026-11-23 00:00:00+00')""")
    con.execute("DROP TABLE mf_paper_systematic_events")
    con.execute("""CREATE TABLE mf_paper_systematic_events (
        instruction_id VARCHAR NOT NULL, sequence INTEGER NOT NULL CHECK (sequence > 0),
        event_id VARCHAR NOT NULL,
        kind VARCHAR NOT NULL CHECK (kind IN ('REDEMPTION_REQUESTED', 'VALUE_CONFIRMED',
            'PAYOUT_AVAILABLE', 'PURCHASE_FUNDS_APPLIED', 'ALLOTMENT_CONFIRMED', 'MISSED')),
        effective_at TIMESTAMPTZ NOT NULL, recorded_at TIMESTAMPTZ NOT NULL,
        basis VARCHAR NOT NULL CHECK (basis IN ('SIMULATED_ASSUMPTION', 'OBSERVED_EVIDENCE')),
        amount_inr DECIMAL(38,18), gross_inr DECIMAL(38,18),
        exit_load_inr DECIMAL(38,18), other_charges_inr DECIMAL(38,18),
        nav_per_unit DECIMAL(38,18), units DECIMAL(38,18), reason VARCHAR,
        source_id VARCHAR, source_ref VARCHAR, source_sha256 VARCHAR,
        event_sha256 VARCHAR NOT NULL, evidence_json VARCHAR NOT NULL,
        PRIMARY KEY (instruction_id, sequence), UNIQUE (instruction_id, event_id),
        UNIQUE (instruction_id, event_sha256)
    )""")
    con.execute("""INSERT INTO mf_paper_systematic_events VALUES (
        'old-flow', 1, 'old-event', 'MISSED', TIMESTAMPTZ '2026-11-23 00:00:00+00',
        TIMESTAMPTZ '2026-11-23 00:00:00+00', 'SIMULATED_ASSUMPTION', NULL, NULL,
        NULL, NULL, NULL, NULL, 'legacy event', NULL, NULL, NULL, 'event-hash', '{}')""")
    con.close()

    Database(cfg).migrate()
    con = duckdb.connect(str(cfg.db_path))
    assert con.execute("SELECT state FROM mf_paper_purchase_eligibility WHERE order_id='old-purchase'").fetchone() == ('NAV_PENDING',)
    assert con.execute("SELECT event_id FROM mf_paper_systematic_events WHERE instruction_id='old-flow'").fetchone() == ('old-event',)
    con.execute("""UPDATE mf_paper_purchase_eligibility SET state='CANCELLED'
        WHERE order_id='old-purchase'""")
    con.execute("""INSERT INTO mf_paper_systematic_events VALUES (
        'old-flow', 2, 'cancel-event', 'PURCHASE_CANCELLED',
        TIMESTAMPTZ '2026-11-24 00:00:00+00', TIMESTAMPTZ '2026-11-24 00:00:00+00',
        'SIMULATED_ASSUMPTION', 10, NULL, NULL, NULL, NULL, NULL, 'cancel',
        NULL, NULL, NULL, 'cancel-hash', '{}')""")
    con.close()
