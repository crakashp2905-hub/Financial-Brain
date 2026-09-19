"""India Implementability Gate: shorts, capacity and costs are judged before a backtest."""
from __future__ import annotations

from datetime import date

import pytest

from financial_brain.config import Config
from financial_brain.evaluation.implementability import FAIL, PASS, UNCHECKED, Hypothesis, check
from financial_brain.storage.db import Database


@pytest.fixture
def con(tmp_path):
    d = Database(Config(data_root=tmp_path).ensure())
    d.migrate()
    with d.connect() as c:
        c.execute("CREATE TABLE features (lineage VARCHAR, business_date DATE, adv20 DOUBLE)")
        # 100 names at Rs 50 cr/day, 400 at Rs 1 cr/day
        c.execute("""INSERT INTO features SELECT 'L' || i, DATE '2026-09-18',
                     CASE WHEN i < 100 THEN 5e8 ELSE 1e7 END FROM range(500) t(i)""")
        yield c


def _h(**kw):
    base = dict(name="momentum", signal="mom_12_1", rebalance_days=20, positions=20,
                aum_inr=5e7, expected_edge_per_rebalance=0.02)
    return Hypothesis(**{**base, **kw})


def test_a_modest_long_only_book_passes_with_surveillance_unchecked(con):
    r = check(con, _h())
    assert r["verdict"] == PASS
    assert r["checks"]["surveillance"]["result"] == UNCHECKED


def test_shorting_fails_closed_without_a_point_in_time_fno_list(con):
    r = check(con, _h(needs_short=True))
    assert r["verdict"] == FAIL and r["checks"]["long_only"]["result"] == FAIL


def test_shorting_passes_when_the_fno_list_covers_the_book_on_that_date(con):
    con.execute("""INSERT INTO security_flags SELECT 'INE' || i, 'NSE', 'FNO_ELIGIBLE',
                   DATE '2026-09-17', NULL, 'NSE', NOW() FROM range(150) t(i)""")
    assert check(con, _h(needs_short=True), as_of=date(2026, 9, 18)
                 )["checks"]["long_only"]["result"] == PASS
    assert check(con, _h(needs_short=True), as_of=date(2025, 1, 1)
                 )["checks"]["long_only"]["result"] == FAIL, "list not valid back then"


def test_capacity_fails_at_large_aum(con):
    # Rs 500 cr in 20 names = Rs 25 cr each; at 5% of ADV needs Rs 500 cr/day - none
    r = check(con, _h(aum_inr=5e9), as_of=date(2026, 9, 18))
    assert r["checks"]["capacity"]["result"] == FAIL


def test_an_edge_smaller_than_costs_fails(con):
    r = check(con, _h(expected_edge_per_rebalance=0.001))
    assert r["checks"]["turnover"]["result"] == FAIL and r["verdict"] == FAIL
