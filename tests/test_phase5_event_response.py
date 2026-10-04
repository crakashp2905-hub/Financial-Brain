from datetime import date

import duckdb
import numpy as np
import pytest

from financial_brain.evaluation import event_response as ER


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("""CREATE TABLE event_responses(news_id VARCHAR, isin VARCHAR, event_type VARCHAR,
                 entry DATE, ar_react DOUBLE, ar1 DOUBLE, ar3 DOUBLE, ar5 DOUBLE, ar20 DOUBLE,
                 vol60 DOUBLE, adv DOUBLE)""")
    rng = np.random.default_rng(0)
    rows = []
    for i in range(400):
        d = date(2020, 1, 1).toordinal() + i
        rows.append((f"n{i}", "X", "ORDER", date.fromordinal(d), 0.02, 0.0, 0.0,
                     float(rng.normal(0.01, 0.02)), 0.0, 0.02, 20.0))
    c.executemany("INSERT INTO event_responses VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    return c


def test_analogues_only_use_closed_outcome_windows(con):
    r = ER.analogues(con, event_type="ORDER", as_of=date(2020, 6, 1), horizon=5)
    assert r["n"] < 400
    assert r["n"] == con.execute(
        "SELECT COUNT(*) FROM event_responses WHERE entry <= DATE '2020-05-18'").fetchone()[0]


def test_too_few_analogues_is_refused_not_quoted(con):
    with pytest.raises(ER.ResponseError):
        ER.analogues(con, event_type="ORDER", as_of=date(2020, 1, 20), horizon=5)


def test_unknown_event_type_and_horizon(con):
    with pytest.raises(ER.ResponseError):
        ER.analogues(con, event_type="NOPE", as_of=date(2021, 1, 1))
    with pytest.raises(ER.ResponseError):
        ER.analogues(con, event_type="ORDER", as_of=date(2021, 1, 1), horizon=7)


def test_matching_falls_back_and_says_so(con):
    r = ER.analogues(con, event_type="ORDER", as_of=date(2021, 6, 1), vol60=0.02, adv=20.0)
    assert r["n"] >= ER.MIN_ANALOGUES and r["se_mean"] > 0


def test_edge_is_measured_against_the_all_event_baseline(con):
    con.execute("INSERT INTO event_responses SELECT 'o'||i, 'X', 'OTHER', DATE '2020-02-01', 0.0, 0.0, 0.0, "
                "0.05, 0.0, 0.02, 20.0 FROM range(200) t(i)")
    r = ER.analogues(con, event_type="ORDER", as_of=date(2021, 6, 1), horizon=5)
    assert r["baseline_mean"] > r["mean"]                    # the baseline includes the +5% OTHER events
    assert r["edge_over_baseline"] == pytest.approx(r["mean"] - r["baseline_mean"])
