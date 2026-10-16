"""Schema migrations: a fresh database, a re-run, and a database from an earlier version all end at the same schema."""
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
    con.close()
    Database(cfg).migrate()
    assert _schema(cfg.db_path) == fresh
