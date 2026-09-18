"""Phase 2 - adjusted continuous prices and features."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from financial_brain.config import Config
from financial_brain.storage.db import Database

OLD, NEW = "INE513A01014", "INE513A01022"              # Schaeffler 1:5, ex 2022-02-08
EX = date(2022, 2, 8)


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(Config(data_root=tmp_path).ensure())
    d.migrate()
    return d


def _px(con, d, isin, close):
    con.execute("""INSERT INTO universe_snapshots (business_date, isin, exchange, ticker,
        series, instrument_type, turnover, close_price, tradable)
        VALUES (?, ?, 'NSE', 'SCHAEFFLER', 'EQ', 'STK', 1e8, ?, TRUE)""", [d, isin, close])


def _schaeffler(con, n_before=30, n_after=5):
    days = [EX - timedelta(days=n_before - i) for i in range(n_before)]
    for i, d in enumerate(days):
        _px(con, d, OLD, 9000.0 + i)                  # pre-split, ~9,000
    for i in range(n_after):
        _px(con, EX + timedelta(days=i), NEW, 1800.0 + i)   # post-split, ~1,800
    con.execute("""INSERT INTO isin_successions VALUES (?, ?, ?, 'BSE+NSE', 0.2,
                   'corroborated', 'test', NOW())""", [OLD, NEW, EX])
    con.execute("""INSERT INTO adjustment_factors (isin, effective_from, price_factor,
                   volume_factor, derived_from, computed_at)
                   VALUES (?, ?, 0.2, 5.0, 'test', NOW())""", [NEW, EX])


def test_one_continuous_series_across_the_split(db):
    from financial_brain.features import prices
    with db.connect() as con:
        _schaeffler(con)
        prices.build(con)
        rows = con.execute("SELECT business_date, isin, close_raw, close_adj FROM "
                           "adjusted_prices WHERE lineage = ? ORDER BY 1", [NEW]).fetchall()
    assert len(rows) == 35 and {r[1] for r in rows} == {OLD, NEW}, "one lineage, both ISINs"
    before, after = [r for r in rows if r[0] == EX - timedelta(days=1)][0], \
        [r for r in rows if r[0] == EX][0]
    assert before[3] == pytest.approx(before[2] * 0.2), "pre-split prices divided by 5"
    assert after[3] == after[2], "post-split prices untouched"
    assert abs(after[3] / before[3] - 1) < 0.02, "no artificial -80% day"


def test_features_have_no_split_day_crash_and_no_short_windows(db):
    from financial_brain.features import indicators
    with db.connect() as con:
        _schaeffler(con)
        indicators.build(con)
        r = con.execute("SELECT ret_1d, vol_20, above_ma200, n_obs FROM features "
                        "WHERE lineage = ? AND business_date = ?", [NEW, EX]).fetchone()
    ret_1d, vol_20, above_ma200, n_obs = r
    assert abs(ret_1d) < 0.02, ret_1d
    assert vol_20 is not None and vol_20 < 0.5, "a split must not look like volatility"
    assert above_ma200 is None and n_obs == 31, "200-session feature is NULL on 31 sessions"


def test_a_feature_never_sees_a_later_price(db):
    """Changing a later close must not change an earlier feature (beyond adjustment)."""
    from financial_brain.features import indicators
    with db.connect() as con:
        _schaeffler(con)
        indicators.build(con)
        before = con.execute("SELECT ret_5d, vol_20 FROM features WHERE lineage = ? "
                             "AND business_date = ?", [NEW, EX - timedelta(days=3)]).fetchone()
        con.execute("UPDATE universe_snapshots SET close_price = close_price * 3 "
                    "WHERE isin = ? AND business_date > ?", [NEW, EX])
        indicators.build(con)
        after = con.execute("SELECT ret_5d, vol_20 FROM features WHERE lineage = ? "
                            "AND business_date = ?", [NEW, EX - timedelta(days=3)]).fetchone()
    assert before == pytest.approx(after)
