from datetime import date

import duckdb
import pytest

from financial_brain.risk import exposure


@pytest.fixture
def con():
    db = duckdb.connect(":memory:")
    db.execute("""CREATE TABLE paper_trades (
        decision_id VARCHAR, isin VARCHAR, status VARCHAR, weight DOUBLE,
        entry_date DATE, entry_price DOUBLE, bucket VARCHAR, excess DOUBLE)""")
    db.execute("CREATE TABLE security_listings (isin VARCHAR, ticker VARCHAR)")
    db.execute("CREATE TABLE promoter_groups (group_id VARCHAR, anchor VARCHAR, member VARCHAR)")
    db.execute("CREATE TABLE security_lineage (isin VARCHAR, lineage VARCHAR)")
    db.execute("""CREATE TABLE features (
        business_date DATE, lineage VARCHAR, mom_12_1 DOUBLE, dist_52w_high DOUBLE,
        vol_60 DOUBLE, ret_20d DOUBLE, adv20 DOUBLE)""")
    day1, day2 = date(2026, 1, 5), date(2026, 1, 6)
    db.executemany("INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?)", [
        ("a", "ISIN-A", "open", 0.25, day1, 100.0, "large", 0.01),
        ("b", "ISIN-B", "closed", 0.25, day2, 200.0, "small", -0.02),
        ("c", "ISIN-C", "open", 0.50, day2, 300.0, "micro", 0.03),
    ])
    db.executemany("INSERT INTO security_listings VALUES (?,?)", [
        ("ISIN-A", "AAA"), ("ISIN-B", "BBB"), ("ISIN-C", "CCC")])
    db.executemany("INSERT INTO promoter_groups VALUES (?,?,?)", [
        ("G1", "PROMOTER-1", "ISIN-A"), ("G1", "PROMOTER-1", "ISIN-B")])
    db.executemany("INSERT INTO security_lineage VALUES (?,?)", [
        ("ISIN-A", "LA"), ("ISIN-B", "LB"), ("ISIN-C", "LC")])
    factors = []
    for day in (day1, day2):
        for index, lineage in enumerate(("LA", "LB", "LC")):
            factors.append((day, lineage, float(index), float(index) / 10,
                            float(index + 1), float(index) / 100, float(index + 10)))
    db.executemany("INSERT INTO features VALUES (?,?,?,?,?,?,?)", factors)
    yield db
    db.close()


def test_open_book_and_historical_window_are_distinct(con):
    open_book = exposure.positions(con)
    assert [p["decision_id"] for p in open_book] == ["a", "c"]
    assert [p["ticker"] for p in open_book] == ["AAA", "CCC"]
    historical = exposure.positions(con, date(2026, 1, 6), window_days=2)
    assert [p["decision_id"] for p in historical] == ["a", "b", "c"]
    assert exposure.positions(con, date(2026, 1, 6), window_days=0) == []


def test_exposure_report_retains_unclassified_and_illiquid_weight(con):
    report = exposure.report(con, date(2026, 1, 6))
    assert report["positions"] == 2
    assert report["gross_weight"] == pytest.approx(0.75)
    assert report["by_group"] == {"UNCLASSIFIED": pytest.approx(2 / 3),
                                  "PROMOTER-1": pytest.approx(1 / 3)}
    assert report["by_tier"] == {"micro": pytest.approx(2 / 3),
                                 "large": pytest.approx(1 / 3)}
    assert report["illiquid_share"] == pytest.approx(2 / 3)
    assert report["largest_group"] == ("UNCLASSIFIED", pytest.approx(2 / 3))
    assert report["industry_available"] is False
    assert report["by_industry"] is None
    assert report["factor_tilts"]["_names_scored"] == 2
    assert report["factor_tilts"]["mom_12_1"] == pytest.approx(0.5)


def test_empty_book_has_no_division_by_zero_or_invented_factor_tilts(con):
    con.execute("DELETE FROM paper_trades")
    assert exposure.by_group([]) == {}
    assert exposure.by_tier([]) == {}
    assert exposure.factor_tilts(con, []) == {}
    report = exposure.report(con, date(2026, 1, 6))
    assert report["positions"] == 0
    assert report["gross_weight"] == 0
    assert report["largest_group"] is None
    assert report["factor_tilts"] == {}
