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



@pytest.fixture(autouse=True)
def _no_real_model(monkeypatch):
    """The learned challenger reads a trained model and a cache under data/; these tests must not."""
    from financial_brain.evaluation import learned_model as LM

    def none(*a, **k):
        raise LM.LearnedModelError("no model in tests")
    monkeypatch.setattr(LM, "score_day", none)


def test_nothing_before_the_lock_can_be_recorded():
    r, _ = research(5)
    with pytest.raises(C.ChampionError):
        C.record(r, duckdb.connect(), date(2026, 10, 1))


def test_record_writes_every_strategy_with_the_champion_among_them():
    r, cal = research(5)
    led = duckdb.connect()
    n = C.record(r, led, cal[0])
    assert C.CHAMPION in n and "composite_top" in n and n[C.CHAMPION] == 40


def test_scanner_challenger_uses_recorded_session_not_lock_date(monkeypatch):
    r, cal = research(5)
    seen = []
    original = C.S.scan

    def watch(cs, as_of, *args, **kwargs):
        seen.append(as_of)
        return original(cs, as_of, *args, **kwargs)

    monkeypatch.setattr(C.S, "scan", watch)
    C.record(r, duckdb.connect(), cal[2])
    assert seen == [cal[2]]


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
    assert all(s["status"] == "INSUFFICIENT_EVIDENCE" for s in C.standings(led))


def test_the_standings_detect_a_challenger_that_is_worse_than_the_champion():
    led = duckdb.connect()
    led.execute(C.SCHEMA)
    rows = []
    for k in range(C.MIN_COHORTS * C.HORIZON):
        day = date.fromordinal(date(2026, 10, 5).toordinal() + k).isoformat()
        rows.append((day, C.CHAMPION, 40, 0.02 + 0.001 * np.sin(k), 0.003, 0.017 + 0.001 * np.sin(k), 0.01))
        rows.append((day, "composite_top", 40, 0.01 + 0.001 * np.cos(k), 0.003, 0.007 + 0.001 * np.cos(k), 0.01))
    led.executemany("INSERT INTO champion_cohorts VALUES (?,?,?,?,?,?,?)", rows)
    C._cohort_columns(led)
    led.execute("UPDATE champion_cohorts SET complete = TRUE, metric_version = ?", [C.EXECUTABLE_METRIC])
    schedule = [rows[k * C.HORIZON * 2][0] for k in range(C.MIN_COHORTS)]
    s = {x["strategy"]: x for x in C.standings(
        led, scheduled_days=schedule, eligible_days=set(schedule))}["composite_top"]
    assert s["cohorts"] == C.MIN_COHORTS and s["status"] == "WORSE_THAN_CHAMPION"


def test_missing_middle_day_does_not_shift_precommitted_schedule():
    led = duckdb.connect()
    led.execute(C.SCHEMA)
    C._cohort_columns(led)
    days = [date(2026, 10, 5 + k).isoformat() for k in range(4)]
    for day in (days[0], days[2], days[3]):
        for strategy, net in ((C.CHAMPION, .01), ("challenger", .02)):
            led.execute("""INSERT INTO champion_cohorts
                (day, strategy, n, gross, cost, net, universe, complete, metric_version)
                VALUES (?, ?, 1, ?, 0, ?, 0, TRUE, ?)""", [day, strategy, net, net, C.EXECUTABLE_METRIC])
    report = {x["strategy"]: x for x in C.standings(
        led, horizon=1, scheduled_days=days[:3], eligible_days=set(days))}["challenger"]
    assert report["cohorts"] == 2
    assert report["scheduled_cohorts"] == 3
    assert report["withheld_cohorts"] == 1
    assert C.standings(led)[0]["cohorts"] == 0


def test_legacy_and_incomplete_rows_never_enter_promotion_statistics():
    led = duckdb.connect()
    led.execute(C.SCHEMA)
    C._cohort_columns(led)
    days = [date(2026, 10, 5 + k).isoformat() for k in range(3)]
    for day in days:
        led.execute("""INSERT INTO champion_cohorts
            (day, strategy, n, gross, cost, net, universe, complete, metric_version)
            VALUES (?, ?, 1, 0, 0, 0, 0, TRUE, ?)""", [day, C.CHAMPION, C.EXECUTABLE_METRIC])
    for day, net, complete in ((days[0], 1000., None), (days[1], .5, False), (days[2], .01, True)):
        led.execute("""INSERT INTO champion_cohorts
            (day, strategy, n, gross, cost, net, universe, complete, metric_version)
            VALUES (?, 'challenger', 1, ?, 0, ?, 0, ?, ?)""",
                    [day, net, net, complete, C.EXECUTABLE_METRIC])
    report = {x["strategy"]: x for x in C.standings(
        led, horizon=1, scheduled_days=days, eligible_days=set(days))}["challenger"]
    assert report["cohorts"] == 1 and report["legacy_cohorts"] == 1
    assert report["incomplete_cohorts"] == 1 and report["withheld_cohorts"] == 2
    assert report["mean_net_vs_champion"] == pytest.approx(.01)
    assert report["status"] != "PROMOTE"


def test_champion_cost_is_fractional_return_not_rupees_or_bps():
    from financial_brain.costs.india import CostModel

    expected = CostModel().round_trip_sized(order_value=100_000, adv_value=10_000_000,
                                            daily_vol=.02)["total"]
    led, r = duckdb.connect(), duckdb.connect()
    led.execute(C.SCHEMA)
    led.execute("INSERT INTO champion_picks VALUES ('2026-10-05', ?, 'A', 'LA', ?)",
                [C.CHAMPION, expected])
    r.execute("CREATE TABLE adjusted_prices(lineage VARCHAR, business_date DATE, close_adj DOUBLE)")
    r.executemany("INSERT INTO adjusted_prices VALUES (?,?,?)", [
        ("LA", date(2026, 10, 5), 100.), ("LA", date(2026, 10, 6), 102.)])
    C.resolve(led, r, horizon=1)
    cost, net = led.execute("SELECT cost, net FROM champion_cohorts").fetchone()
    assert 0 < cost < .1 and cost == pytest.approx(expected)
    assert net == pytest.approx(.02 - expected)


def test_duplicate_or_unsorted_schedule_is_refused():
    led = duckdb.connect()
    led.execute(C.SCHEMA)
    led.execute("INSERT INTO champion_cohorts VALUES ('2026-10-05', 'challenger', 1, 0, 0, 0, 0)")
    with pytest.raises(C.ChampionError, match="scheduled_days"):
        C.standings(led, scheduled_days=["2026-10-06", "2026-10-05"])
    with pytest.raises(C.ChampionError, match="scheduled_days"):
        C.standings(led, scheduled_days=["2026-10-05", "2026-10-05"])


def test_close_to_close_ranking_metric_cannot_be_promoted():
    led = duckdb.connect()
    led.execute(C.SCHEMA)
    C._cohort_columns(led)
    day = "2026-10-05"
    for strategy, net in ((C.CHAMPION, 0.), ("challenger", 100.)):
        led.execute("""INSERT INTO champion_cohorts
            (day, strategy, n, gross, cost, net, universe, complete, metric_version)
            VALUES (?, ?, 1, ?, 0, ?, 0, TRUE, ?)""", [day, strategy, net, net, C.RANKING_METRIC])
    report = {x["strategy"]: x for x in C.standings(
        led, horizon=1, scheduled_days=[day], eligible_days={day})}["challenger"]
    assert report["cohorts"] == 0 and report["diagnostic_cohorts"] == 1
    assert report["non_executable_cohorts"] == 1 and report["status"] != "PROMOTE"


def test_duplicate_lineage_aliases_do_not_double_count_return():
    led, r = duckdb.connect(), duckdb.connect()
    led.execute(C.SCHEMA)
    led.executemany("INSERT INTO champion_picks VALUES (?,?,?,?,?)", [
        ("2026-10-05", C.CHAMPION, "OLD", "LA", .01),
        ("2026-10-05", C.CHAMPION, "NEW", "LA", .01),
    ])
    r.execute("CREATE TABLE adjusted_prices(lineage VARCHAR, business_date DATE, close_adj DOUBLE)")
    r.executemany("INSERT INTO adjusted_prices VALUES (?,?,?)", [
        ("LA", date(2026, 10, 5), 100.), ("LA", date(2026, 10, 6), 110.)])
    C.resolve(led, r, horizon=1)
    selected, missing, complete = led.execute(
        "SELECT selected_n, missing_n, complete FROM champion_cohorts").fetchone()
    assert (selected, missing, complete) == (2, 2, False)


def test_duplicated_price_lineage_is_unresolved_not_last_row_wins():
    led, r = duckdb.connect(), duckdb.connect()
    led.execute(C.SCHEMA)
    led.execute("INSERT INTO champion_picks VALUES ('2026-10-05', ?, 'A', 'LA', .01)", [C.CHAMPION])
    r.execute("CREATE TABLE adjusted_prices(lineage VARCHAR, business_date DATE, close_adj DOUBLE)")
    r.executemany("INSERT INTO adjusted_prices VALUES (?,?,?)", [
        ("LA", date(2026, 10, 5), 100.),
        ("LA", date(2026, 10, 6), 110.),
        ("LA", date(2026, 10, 6), 120.),
    ])
    C.resolve(led, r, horizon=1)
    n, missing, complete = led.execute(
        "SELECT n, missing_n, complete FROM champion_cohorts").fetchone()
    assert (n, missing, complete) == (0, 1, False)


def test_missing_selected_price_is_counted_and_never_silently_dropped():
    led, research_con = duckdb.connect(), duckdb.connect()
    led.execute(C.SCHEMA)
    d0, d1 = date(2026, 10, 5), date(2026, 10, 6)
    led.executemany("INSERT INTO champion_picks VALUES (?,?,?,?,?)", [
        (str(d0), C.CHAMPION, "A", "LA", .01),
        (str(d0), C.CHAMPION, "B", "LB", .01),
    ])
    research_con.execute("CREATE TABLE adjusted_prices(lineage VARCHAR, business_date DATE, close_adj DOUBLE)")
    research_con.executemany("INSERT INTO adjusted_prices VALUES (?,?,?)", [
        ("LA", d0, 100.0), ("LB", d0, 100.0), ("LA", d1, 110.0),
    ])
    assert C.resolve(led, research_con, horizon=1) == 1
    n, selected, missing, complete, worst = led.execute("""SELECT n, selected_n, missing_n, complete,
        worst_case_net FROM champion_cohorts""").fetchone()
    assert (n, selected, missing, complete) == (1, 2, 1, False)
    assert worst == pytest.approx((.1 - 1) / 2 - .01)
    coverage, universe_worst = led.execute("SELECT universe_coverage, universe_worst_case FROM champion_cohorts").fetchone()
    assert coverage == .5 and universe_worst == pytest.approx(-.45)
    assert C.standings(led, horizon=1)[0]["cohorts"] == 0
