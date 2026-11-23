from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb
import pytest

from financial_brain.ingest.mfnav import as_known_on

IST = ZoneInfo("Asia/Kolkata")


def _connection():
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE mf_nav_observations
        (scheme_code VARCHAR, nav_date DATE, nav DOUBLE, source_document_sha256 VARCHAR,
         source_published_at TIMESTAMPTZ, retrieved_at TIMESTAMPTZ, observed_at TIMESTAMPTZ,
         lake_key VARCHAR, record_sha256 VARCHAR)""")
    con.execute("""INSERT INTO mf_nav_observations VALUES
        ('S1','2026-11-23',100,'abc',NULL,'2026-11-23 14:00:00+05:30',
         '2026-11-23 14:00:00+05:30','lake/key','record')""")
    return con


def test_same_day_nav_without_publication_time_is_refused_before_cutoff() -> None:
    con = _connection()
    try:
        before = datetime(2026, 11, 23, 20, 59, tzinfo=IST)
        assert as_known_on(con, "S1", date(2026, 11, 23), before) is None
        assert as_known_on(con, "S1", date(2026, 11, 23), before,
                           same_day_nav_cutoff=datetime(2026, 11, 23, 21, 0, tzinfo=IST)) is None
        after = datetime(2026, 11, 23, 21, 1, tzinfo=IST)
        row = as_known_on(con, "S1", date(2026, 11, 23), after,
                          same_day_nav_cutoff=datetime(2026, 11, 23, 21, 0, tzinfo=IST))
        assert row["nav"] == pytest.approx(100)
    finally:
        con.close()


def test_same_day_cutoff_must_be_timezone_aware_and_on_nav_date() -> None:
    con = _connection()
    try:
        with pytest.raises(ValueError, match="aware and fall on nav_date"):
            as_known_on(con, "S1", date(2026, 11, 23),
                        datetime(2026, 11, 23, 22, 0, tzinfo=IST),
                        same_day_nav_cutoff=datetime(2026, 11, 23, 21, 0))
    finally:
        con.close()
