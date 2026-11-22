from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import duckdb
import pytest

from financial_brain.features.index_cross_section import IndexFeatureError, build_index_features
from financial_brain.ingest.index_tri import LISTS

UTC = timezone.utc


def _database(*, n_sessions: int = 31):
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE index_membership (
        index_name VARCHAR, as_of DATE, symbol VARCHAR, isin VARCHAR, industry VARCHAR,
        observed_at TIMESTAMPTZ)""")
    con.execute("""CREATE TABLE index_total_return (
        index_name VARCHAR, business_date DATE, tri DOUBLE, ntr DOUBLE, observed_at TIMESTAMPTZ)""")
    con.execute("""CREATE TABLE adjusted_prices (
        isin VARCHAR, lineage VARCHAR, business_date DATE)""")
    con.execute("""CREATE TABLE features (
        lineage VARCHAR, business_date DATE, ret_20d DOUBLE, above_ma200 BOOLEAN)""")
    session = date(2026, 6, 30)
    snapshot = session - timedelta(days=1)
    seen = datetime(2026, 6, 29, 10, tzinfo=UTC)
    for name, prefix, industries in (
        ("NIFTY 50", "N", ["Banks", "Banks", "IT", "Energy"]),
        ("NIFTY BANK", "B", ["Banks", "Banks", "Finance", "Finance"]),
    ):
        for i, industry in enumerate(industries):
            isin, symbol, lineage = f"{prefix}{i}", f"{prefix}SYM{i}", f"L{prefix}{i}"
            con.execute("INSERT INTO index_membership VALUES (?,?,?,?,?,?)",
                        [name, snapshot, symbol, isin, industry, seen])
            con.execute("INSERT INTO adjusted_prices VALUES (?,?,?)", [isin, lineage, session])
            con.execute("INSERT INTO features VALUES (?,?,?,?)",
                        [lineage, session, (i - 1.5) / 100, i % 2 == 0])

    tri_observed = datetime(2026, 6, 30, 11, tzinfo=UTC)
    indexes = ("NIFTY 50", "NIFTY BANK", "NIFTY IT")
    values = {name: 1000.0 for name in indexes}
    for i in range(n_sessions):
        d = session - timedelta(days=n_sessions - i - 1)
        move = [0.01, -0.004, 0.006, 0.002, -0.003][i % 5]
        values["NIFTY 50"] *= 1 + move
        values["NIFTY BANK"] *= 1 + (move * 1.4 + 0.0003)
        values["NIFTY IT"] *= 1 + (move + 0.002)
        for name in indexes:
            con.execute("INSERT INTO index_total_return VALUES (?,?,?,?,?)",
                        [name, d, values[name], None, tri_observed])
    return con, session


def test_cross_index_breadth_concentration_correlation_and_sector_strength_align():
    con, session = _database()
    result = build_index_features(con, session=session,
                                  decision_at=datetime(2026, 6, 30, 12, tzinfo=UTC),
                                  correlation_lookback=20, relative_strength_lookback=10,
                                  min_member_coverage=0.75)
    con.close()
    assert result["status"] == "AVAILABLE"
    assert result["membership"]["NIFTY 50"]["matched_members"] == 4
    assert result["membership"]["NIFTY 50"]["pct_up_20d"] == pytest.approx(0.5)
    assert result["membership"]["NIFTY BANK"]["pct_above_ma200"] == pytest.approx(0.5)
    assert result["constituent_sector_concentration"]["NIFTY BANK"]["largest_industry_share"] == 0.5
    assert result["nifty_bank_correlation"]["observations"] == 20
    assert result["nifty_bank_correlation"]["value"] > 0.95
    it = next(row for row in result["sector_relative_strength"] if row["index"] == "NIFTY IT")
    assert it["status"] == "AVAILABLE" and it["relative_return"] > 0


def test_future_publication_and_future_membership_are_not_visible():
    con, session = _database(n_sessions=15)
    con.execute("INSERT INTO index_membership VALUES ('NIFTY BANK', ?, 'FUTURE', 'FUTURE_ISIN', 'Banks', ?) ",
                [session + timedelta(days=1), datetime(2026, 6, 29, 10, tzinfo=UTC)])
    result = build_index_features(con, session=session,
                                  decision_at=datetime(2026, 6, 30, 12, tzinfo=UTC),
                                  correlation_lookback=20)
    con.close()
    assert result["membership"]["NIFTY BANK"]["members"] == 4
    assert result["nifty_bank_correlation"]["value"] is None
    assert result["nifty_bank_correlation"]["status"] == "INSUFFICIENT_ALIGNED_TRI_HISTORY"


def test_missing_membership_is_unavailable_and_naive_decision_is_rejected():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE index_membership (index_name VARCHAR, as_of DATE, symbol VARCHAR, isin VARCHAR, industry VARCHAR, observed_at TIMESTAMPTZ)")
    con.execute("CREATE TABLE adjusted_prices (isin VARCHAR, lineage VARCHAR, business_date DATE)")
    con.execute("CREATE TABLE features (lineage VARCHAR, business_date DATE, ret_20d DOUBLE, above_ma200 BOOLEAN)")
    out = build_index_features(con, session=date(2026, 6, 30), decision_at=datetime(2026, 6, 30, 12, tzinfo=UTC))
    assert out["membership"]["NIFTY 50"]["status"] == "MEMBERSHIP_UNAVAILABLE"
    with pytest.raises(IndexFeatureError, match="timezone-aware"):
        build_index_features(con, session=date(2026, 6, 30), decision_at=datetime(2026, 6, 30, 12))
    con.close()


def test_bank_constituent_snapshot_is_requested_from_the_existing_provider():
    assert LISTS["NIFTY BANK"] == "niftybank"
