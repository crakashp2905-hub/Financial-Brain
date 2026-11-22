from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import duckdb

from financial_brain.features.event_availability import build

UTC = timezone.utc


def _db():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE adjusted_prices (lineage VARCHAR, business_date DATE)")
    con.execute("CREATE TABLE security_lineage (isin VARCHAR, lineage VARCHAR)")
    con.execute("CREATE TABLE features (lineage VARCHAR, business_date DATE)")
    con.execute("""CREATE TABLE announcements (
        isin VARCHAR, news_id VARCHAR, event_type VARCHAR, published_at TIMESTAMP,
        observed_at TIMESTAMPTZ, source VARCHAR)""")
    con.execute("""CREATE TABLE xbrl_filings (
        isin VARCHAR, seq_number VARCHAR, filed_at TIMESTAMP, attempted_at TIMESTAMPTZ)""")
    con.execute("""CREATE TABLE shareholding_filings (
        isin VARCHAR, record_id VARCHAR, known_at TIMESTAMP, observed_at TIMESTAMPTZ)""")
    con.execute("""CREATE TABLE fundamental_observation (
        isin VARCHAR, seq_number VARCHAR, filed_at TIMESTAMP, period_end DATE,
        basis VARCHAR, revision INTEGER)""")
    sessions = [date(2026, 10, 26) + timedelta(days=i) for i in (0, 1, 2, 3, 4, 7)]
    for session in sessions:
        for lineage in ("L1", "L2"):
            con.execute("INSERT INTO adjusted_prices VALUES (?,?)", [lineage, session])
            con.execute("INSERT INTO features VALUES (?,?)", [lineage, session])
    con.executemany("INSERT INTO security_lineage VALUES (?,?)", [("I1", "L1"), ("I2", "L2")])
    observed = datetime(2026, 10, 29, 12, tzinfo=UTC)
    con.executemany("INSERT INTO announcements VALUES (?,?,?,?,?,?)", [
        ("I1", "BSE-EARLY", "GENERAL", datetime(2026, 10, 26, 8, 30), observed, "BSE"),
        ("I1", "BSE-RESULT", "RESULTS", datetime(2026, 10, 26, 18), observed, "BSE"),
        ("I2", "BSE-FRIDAY", "GENERAL", datetime(2026, 10, 30, 16), observed, "BSE"),
    ])
    con.execute("INSERT INTO xbrl_filings VALUES ('I1','NSE-R1',?,?)",
                [datetime(2026, 10, 27, 10), observed])
    con.execute("INSERT INTO shareholding_filings VALUES ('I1','SHP1',?,?)",
                [datetime(2026, 10, 27, 8), observed])
    con.execute("INSERT INTO fundamental_observation VALUES ('I1','REV2',?,DATE '2026-09-30','consolidated',2)",
                [datetime(2026, 10, 28, 18)])
    return con


def test_event_families_keep_source_time_and_map_after_open_to_next_session():
    con = _db()
    result = build(con)
    rows = con.execute("""SELECT event_id, event_type, available_session
        FROM event_availability ORDER BY event_id""").fetchall()
    assert result["events"] == 6
    assert ("BSE-EARLY", "CORPORATE_ANNOUNCEMENT", date(2026, 10, 26)) in rows
    assert ("BSE-RESULT", "FINANCIAL_RESULT", date(2026, 10, 27)) in rows
    assert ("NSE-R1", "FINANCIAL_RESULT", date(2026, 10, 28)) in rows
    assert ("SHP1", "SHAREHOLDING_UPDATE", date(2026, 10, 27)) in rows
    assert ("REV2", "STATEMENT_REVISION", date(2026, 10, 29)) in rows
    assert ("BSE-FRIDAY", "CORPORATE_ANNOUNCEMENT", date(2026, 11, 2)) in rows
    by_day = con.execute("""SELECT business_date, announcements_5s, results_5s,
        shareholding_updates_60s, statement_revisions_60s, sessions_since_result
        FROM event_availability_features WHERE lineage = 'L1' ORDER BY business_date""").fetchall()
    assert by_day[0][1:5] == (1, 0, 0, 0)
    assert by_day[1][1:5] == (1, 1, 1, 0)
    assert by_day[2][2:5] == (2, 1, 0)
    assert by_day[3][4] == 1
    assert result["by_type"] == {
        "CORPORATE_ANNOUNCEMENT": 2,
        "FINANCIAL_RESULT": 2,
        "SHAREHOLDING_UPDATE": 1,
        "STATEMENT_REVISION": 1,
    }
    con.close()


def test_no_source_tables_creates_empty_feature_surface_without_inventing_events():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE adjusted_prices (lineage VARCHAR, business_date DATE)")
    con.execute("INSERT INTO adjusted_prices VALUES ('L1', DATE '2026-10-26')")
    con.execute("CREATE TABLE security_lineage (isin VARCHAR, lineage VARCHAR)")
    con.execute("CREATE TABLE features (lineage VARCHAR, business_date DATE)")
    con.execute("INSERT INTO features VALUES ('L1', DATE '2026-10-26')")
    result = build(con)
    assert result["events"] == 0
    assert result["feature_rows"] == 1
    assert result["by_type"] == {}
    con.close()
