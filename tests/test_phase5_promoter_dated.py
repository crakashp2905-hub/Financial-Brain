from datetime import date

import duckdb
import pytest

from financial_brain.graph import promoter_dated as P


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("CREATE TABLE security_lineage(isin VARCHAR, lineage VARCHAR)")
    c.execute("INSERT INTO security_lineage VALUES ('A','A'),('B','B'),('C','C')")
    c.execute("""CREATE TABLE holder_filings(isin VARCHAR, filer_key VARCHAR, filer_kind VARCHAR,
                 relation VARCHAR, business_date DATE)""")
    c.execute("""INSERT INTO holder_filings VALUES
        ('A','p1','person','PLEDGE','2020-01-10'),('B','p1','person','PLEDGE','2020-03-01'),
        ('A','lic','institution','PLEDGE','2020-01-10'),('C','lic','institution','PLEDGE','2020-01-10')""")
    c.execute("CREATE TABLE features AS SELECT DATE '2019-12-01' + CAST(i AS INTEGER) AS business_date FROM range(900) t(i)")
    return c


def test_institutions_do_not_link(con):
    assert P.build(con) == 1
    assert con.execute("SELECT a, b FROM promoter_links").fetchone() == ("A", "B")


def test_link_not_visible_before_first_filing_and_lapses(con):
    P.build(con)
    P.membership(con, ttl=100)
    dates = {r[0] for r in con.execute(
        "SELECT business_date FROM promoter_siblings WHERE lineage='A'").fetchall()}
    assert date(2020, 2, 29) not in dates      # before the evidencing filing (2020-03-01)
    assert date(2020, 3, 1) in dates
    assert date(2020, 6, 9) in dates and date(2020, 6, 11) not in dates   # 100d after last filing


def test_empty_filters_refused(con):
    with pytest.raises(P.GraphError):
        P.build(con, kinds=())
