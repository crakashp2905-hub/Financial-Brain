from datetime import date

import duckdb
import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import champion as C


def research(days=60, n=200, seed=0, edge=None):
    rng = np.random.default_rng(seed)
    con = duckdb.connect()
    cal = pd.bdate_range("2026-10-05", periods=days).date
    mom = rng.normal(0, .3, n)
    rows_f, rows_p = [], []
    for k, d in enumerate(cal):
        for i in range(n):
            drift = 0.0
            if edge == "momentum":
                drift = 0.002 * mom[i]
            px = 100 * np.exp(drift * k + rng.normal(0, 0.002))
            rows_p.append((f"L{i}", f"T{i}", d, px, 2e8))
            rows_f.append((f"L{i}", d, 2e8, 0.25 + 0.001 * i, mom[i], -0.1, 0.0, 0.0, 20.0, 0.5, 0.02))
    con.execute("CREATE TABLE adjusted_prices(lineage VARCHAR, ticker VARCHAR, business_date DATE, close_adj DOUBLE, turnover DOUBLE)")
    con.executemany("INSERT INTO adjusted_prices VALUES (?,?,?,?,?)", rows_p)
    con.execute("""CREATE TABLE features(lineage VARCHAR, business_date DATE, adv20 DOUBLE, vol_60 DOUBLE, mom_12_1 DOUBLE,
                   dist_52w_high DOUBLE, ret_20d DOUBLE, ret_1d DOUBLE, adx_14 DOUBLE, atr_pctile_250 DOUBLE, atr_14_pct DOUBLE)""")
    con.executemany("INSERT INTO features VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows_f)
    con.execute("ALTER TABLE features ADD COLUMN ret_5d DOUBLE")
    con.execute("ALTER TABLE features ADD COLUMN rsi_14 DOUBLE")
    con.execute("ALTER TABLE adjusted_prices ADD COLUMN isin VARCHAR")
    return con, cal


def test_nothing_before_the_lock_can_be_recorded():
    r, _ = research(5)
    with pytest.raises(C.ChampionError):
        C.record(r, duckdb.connect(), date(2026, 10, 1))


def test_record_writes_every_strategy_with_the_champion_among_them():
    r, cal = research(5)
    led = duckdb.connect()
    n = C.record(r, led, cal[0])
    assert C.CHAMPION in n and "composite_top" in n and n[C.CHAMPION] == 40


def test_cohorts_resolve_only_after_the_horizon_and_pay_their_costs():
    r, cal = research(30)
    led = duckdb.connect()
    C.record(r, led, cal[0])
    C.record(r, led, cal[15])
    assert C.resolve(led, r) == 1                          # the second cohort's 20 sessions have not passed
    row = led.execute("SELECT gross, cost, net FROM champion_cohorts WHERE strategy = ?", [C.CHAMPION]).fetchone()
    assert row[2] == pytest.approx(row[0] - row[1]) and row[1] > 0


def test_until_a_year_of_cohorts_exists_every_challenger_is_insufficient():
    r, cal = research(45)
    led = duckdb.connect()
    for d in cal[:21]:
        C.record(r, led, d)
    C.resolve(led, r)
    assert all(s["status"] == "INSUFFICIENT_DATA" for s in C.standings(led))


def test_the_standings_detect_a_challenger_that_is_worse_than_the_champion():
    led = duckdb.connect()
    led.execute(C.SCHEMA)
    rows = []
    for k in range(C.MIN_COHORTS * C.HORIZON):
        day = date.fromordinal(date(2026, 10, 5).toordinal() + k).isoformat()
        rows.append((day, C.CHAMPION, 40, 0.02 + 0.001 * np.sin(k), 0.003, 0.017 + 0.001 * np.sin(k), 0.01))
        rows.append((day, "composite_top", 40, 0.01 + 0.001 * np.cos(k), 0.003, 0.007 + 0.001 * np.cos(k), 0.01))
    led.executemany("INSERT INTO champion_cohorts VALUES (?,?,?,?,?,?,?)", rows)
    s = {x["strategy"]: x for x in C.standings(led)}["composite_top"]
    assert s["cohorts"] == C.MIN_COHORTS and s["status"] == "WORSE_THAN_CHAMPION"
