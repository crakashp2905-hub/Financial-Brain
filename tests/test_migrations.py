"""Schema migrations: a fresh database, a re-run, and a database from an earlier version all end at the same schema."""
from decimal import Decimal

import duckdb

from financial_brain.config import Config
from financial_brain.storage.db import Database


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
    con.execute("""INSERT INTO mf_paper_ledger_entries VALUES
        ('paper', 'sip-reserve', 1, TIMESTAMPTZ '2026-01-02 00:00:00+00',
         TIMESTAMPTZ '2026-01-02 00:00:00+00', 'SIP_PAYMENT_PENDING', 10, 0,
         'SIMULATED_ASSUMPTION', NULL, NULL, NULL, 'hash-2', '{}')""")
    assert con.execute("""SELECT COUNT(*) FROM mf_paper_ledger_entries
        WHERE account_code='SIP_PAYMENT_PENDING'""").fetchone()[0] == 1
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
