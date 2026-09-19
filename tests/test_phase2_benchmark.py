"""P2-3 evaluation benchmark on a synthetic market with a planted signal."""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pytest

from financial_brain.config import Config
from financial_brain.storage.db import Database


@pytest.fixture
def con(tmp_path):
    d = Database(Config(data_root=tmp_path).ensure())
    d.migrate()
    with d.connect() as c:
        yield c


def _market(con, drift_by_rank: float, n_names=40, n_days=320, seed=7):
    """Each name has a fixed drift; past return therefore predicts future return iff
    drift_by_rank != 0."""
    rnd = random.Random(seed)
    rows = []
    for i in range(n_names):
        isin = f"INE{i:03d}A01010"
        drift = drift_by_rank * (i - n_names / 2) / n_names
        p = 100.0
        for k in range(n_days):
            p *= math.exp(drift + rnd.gauss(0, 0.01))
            rows.append((date(2020, 1, 1) + timedelta(days=k), isin, f"T{i}", p))
    import csv
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as fh:
        csv.writer(fh).writerows([("d", "isin", "ticker", "p"), *rows])
    con.execute(f"CREATE TEMP TABLE df AS SELECT * FROM read_csv('{fh.name}', header=true)")
    con.execute("""INSERT INTO universe_snapshots (business_date, isin, exchange, ticker,
        series, instrument_type, turnover, close_price, tradable)
        SELECT d, isin, 'NSE', ticker, 'EQ', 'STK', 1e9, p, TRUE FROM df""")
    from financial_brain.features import indicators
    indicators.build(con)


def test_planted_momentum_is_found(con):
    from financial_brain.evaluation import benchmark
    _market(con, drift_by_rank=0.02)
    r = benchmark.evaluate(con, "ret_60d", horizon=20)
    assert r["dates"] >= 10 and r["mean_ic"] > 0.5 and r["ic_t"] > 3 and r["mean_spread"] > 0


def test_noise_is_not_found(con):
    from financial_brain.evaluation import benchmark
    _market(con, drift_by_rank=0.0)
    r = benchmark.evaluate(con, "ret_60d", horizon=20)
    assert abs(r["mean_ic"]) < 0.15 and abs(r["ic_t"]) < 2.5


def test_unknown_feature_refused(con):
    from financial_brain.evaluation import benchmark
    with pytest.raises(ValueError):
        benchmark.evaluate(con, "close_adj; DROP TABLE x", 20)


def test_a_name_that_stops_trading_is_scored_not_dropped(con):
    """Survivorship: a crash followed by delisting must count against the signal."""
    from financial_brain.evaluation import benchmark
    _market(con, drift_by_rank=0.0)
    before = benchmark.evaluate(con, "ret_60d", horizon=20)
    # The name crashes 90% on day 200 and never trades again.
    con.execute("""UPDATE universe_snapshots SET close_price = close_price * 0.1
                   WHERE isin = 'INE000A01010' AND business_date = DATE '2020-07-19'""")
    con.execute("""DELETE FROM universe_snapshots WHERE isin = 'INE000A01010'
                   AND business_date > DATE '2020-07-19'""")
    from financial_brain.features import indicators
    indicators.build(con)
    after = benchmark.evaluate(con, "ret_60d", horizon=20)
    assert before["filled_no_outcome"] == 0 and after["filled_no_outcome"] >= 1
    assert after["dates"] == before["dates"], "no rebalance date lost its outcome"
