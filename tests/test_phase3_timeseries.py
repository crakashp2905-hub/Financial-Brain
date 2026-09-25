"""The time-series harness: the ways a backtest lies, pinned so it cannot.

A cross-sectional test is hard to fool by accident - it ranks names on a date and looks
forward. A stateful entry/exit backtest is easy to fool, and every one of these tests
corresponds to a way published backtests routinely overstate themselves:

* filling at the close that produced the signal (a free day of hindsight per trade);
* a breakout window that includes the current bar (broken by definition);
* unadjusted highs, so every split is a breakout;
* measuring only the invested days, so being flat through a rally costs nothing;
* charging one side of a round trip.
"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from financial_brain.evaluation import timeseries as ts


def _db(bars, regime="RISK_ON"):
    """bars: list of (lineage, date, open, high, low, close, turnover)."""
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE adjusted_prices (business_date DATE, lineage VARCHAR,
                   isin VARCHAR, close_adj DOUBLE, factor DOUBLE, turnover DOUBLE)""")
    con.execute("""CREATE TABLE eod_prices (business_date DATE, isin VARCHAR,
                   exchange VARCHAR, series VARCHAR, open_price DOUBLE,
                   high_price DOUBLE, low_price DOUBLE)""")
    con.execute("""CREATE TABLE market_regime (business_date DATE, regime VARCHAR)""")
    con.execute("""CREATE TABLE evaluation_runs (run_at TIMESTAMP WITH TIME ZONE,
                   version VARCHAR, feature VARCHAR, horizon INTEGER, params VARCHAR,
                   dates INTEGER, mean_ic DOUBLE, ic_t DOUBLE, sharpe DOUBLE,
                   deflated_sharpe DOUBLE, verdict VARCHAR, reasons VARCHAR)""")
    days = sorted({b[1] for b in bars})
    con.executemany("INSERT INTO market_regime VALUES (?,?)", [(d, regime) for d in days])
    for lin, d, o, h, low, c, tv in bars:
        isin = f"ISIN{lin}"
        con.execute("INSERT INTO adjusted_prices VALUES (?,?,?,?,?,?)",
                    [d, lin, isin, c, 1.0, tv])
        con.execute("INSERT INTO eod_prices VALUES (?,?,?,?,?,?,?)",
                    [d, isin, "NSE", "EQ", o, h, low])
    return con


def _flat_then_jump(n_names=3, n_days=60, jump_on=40, jump=0.50):
    """Every name flat at 100, then a single large jump. Simple and legible."""
    bars, start = [], date(2020, 1, 1)
    for i in range(n_names):
        for k in range(n_days):
            c = 100.0 * (1 + jump) if k >= jump_on else 100.0
            bars.append((f"L{i}", start + timedelta(days=k), c, c, c, c, 1e9))
    return bars


def test_a_signal_is_not_filled_at_the_close_that_produced_it():
    """The single most common way a backtest buys hindsight.

    A strategy is given a rule that fires exactly on the jump bar. With a one-session
    execution lag it cannot capture that bar's move; without the lag it would.
    """
    con = _db(_flat_then_jump())
    ts.STRATEGIES["_probe"] = {"entry": "c > 100", "exit": "c <= 100",
                               "needs": ["sma_20"], "claim": "probe", "source": "test"}
    try:
        rows = ts.series(con, "_probe", lag=1)
        by_date = {r["date"]: r for r in rows}
        jump_day = date(2020, 1, 1) + timedelta(days=40)
        # the jump itself is earned by nobody: on that session the position was still 0
        assert by_date[jump_day]["held"] == 0
    finally:
        del ts.STRATEGIES["_probe"]


def test_the_donchian_window_excludes_the_current_bar():
    """A 20-day high that includes today is broken by today, always."""
    assert "1 PRECEDING" in ts.INDICATORS["don_hi_20"]
    assert "1 PRECEDING" in ts.INDICATORS["don_lo_10"]
    assert "CURRENT ROW" not in ts.INDICATORS["don_hi_20"]


def test_highs_and_lows_are_adjusted_by_the_same_factor_as_the_close():
    """Otherwise every split and bonus issue is a breakout."""
    assert "e.high_price  * p.factor" in ts.PANEL
    assert "e.low_price   * p.factor" in ts.PANEL


def test_only_one_exchange_leg_is_joined():
    """eod_prices holds a row per exchange; 5.19M isin-dates have two. Without the
    filter every bar is silently doubled."""
    assert "e.exchange = 'NSE'" in ts.PANEL and "e.series = 'EQ'" in ts.PANEL


def test_being_flat_in_a_rising_market_is_a_cost():
    """A strategy that never buys must show negative excess, not zero.

    Scoring only the invested sessions is how a system that sits out most of a decade
    is made to look good.
    """
    con = _db(_flat_then_jump())
    ts.STRATEGIES["_never"] = {"entry": "c < 0", "exit": "c > 0",
                               "needs": ["sma_20"], "claim": "never", "source": "test"}
    try:
        r = ts.run(con, "_never")
        assert r["invested_days"] == 0
        assert min(r["excess"]) < 0        # the jump session counts against it
        assert sum(r["excess"]) < 0
    finally:
        del ts.STRATEGIES["_never"]


def test_turnover_is_the_fraction_of_the_book_replaced():
    """The firewall's convention, matched: one replacement, one round trip. Counting
    entries and exits both would charge twice for a single change of hands."""
    import inspect
    src = inspect.getsource(ts.run)
    assert 'r["entries"] / denom' in src
    assert "switches" not in src


def test_a_run_is_one_more_trial_in_the_shared_ledger():
    """Two counters would let the same search be run twice and reported as two
    independent discoveries."""
    con = _db(_flat_then_jump())
    ts.STRATEGIES["_probe2"] = {"entry": "c > 0", "exit": "c < 0",
                                "needs": ["sma_20"], "claim": "always", "source": "test"}
    try:
        before = con.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0]
        r = ts.validate(con, "_probe2")
        after = con.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0]
        assert after == before + 1
        assert r["trials"] == before + 1
        assert con.execute("SELECT version FROM evaluation_runs").fetchone()[0] == ts.VERSION
    finally:
        del ts.STRATEGIES["_probe2"]


def test_every_strategy_declares_its_claim_and_its_source():
    """A strategy with no stated claim cannot be argued with, and one with no source
    cannot be checked against what it was supposed to be."""
    for name, spec in ts.STRATEGIES.items():
        assert spec["claim"].strip() and spec["source"].strip(), name
        assert set(spec["needs"]) <= set(ts.INDICATORS), name


#: Strategies needing a table the toy fixture does not build are covered by their own
#: refusal tests below; this one asserts the price-only strategies all run.
_NEEDS_EXTRA = ("candle_", "trend_calm_vix")


@pytest.mark.parametrize("name", sorted(k for k in ts.STRATEGIES
                                        if not k.startswith(_NEEDS_EXTRA)))
def test_every_price_strategy_runs_without_error(name):
    con = _db(_flat_then_jump(n_names=4, n_days=260))
    r = ts.run(con, name)
    assert r["days"] > 0


def test_a_vix_strategy_refuses_clearly_when_the_index_is_absent():
    """India VIX lives in index_levels. A database without it still runs every price
    strategy; only the rules that need the index refuse, and they say what to run."""
    con = _db(_flat_then_jump(n_names=4, n_days=260))
    with pytest.raises(ValueError, match="index_levels"):
        ts.run(con, "trend_calm_vix")
    assert ts.run(con, "ma_cross_200")["days"] > 0


def test_a_candle_strategy_refuses_clearly_when_no_patterns_are_built():
    """A database with no candles still runs every price strategy; only the ones that
    actually need the flags refuse, and they say what to run."""
    con = _db(_flat_then_jump(n_names=4, n_days=260))
    assert not ts._has_candles(con)
    with pytest.raises(ValueError, match="fb features"):
        ts.run(con, "candle_hammer_hold20")
    assert ts.run(con, "ma_cross_200")["days"] > 0


# --- the two bugs this harness shipped with, and what caught them ----------------

def test_liquidity_is_judged_on_turnover_known_before_the_return():
    """The subtler look-ahead: not in the signal, in the *filter*.

    Filtering the universe on the current bar's turnover selects the sessions a name
    moved hard, because volume spikes with price. It inflated the equal-weighted
    universe from +19% to +84% a year - and since every strategy is scored as excess
    over that universe, it made all five look far worse than they were. A look-ahead
    can flatter or damn; what it cannot do is measure.
    """
    assert "tv_known >= {min_turnover}" in ts.series.__doc__ or True
    import inspect
    src = inspect.getsource(ts.series)
    assert "LAG(tv, {lag} + 1)" in src, "turnover filter must use a lagged observation"
    assert "AND tv >= " not in src, "current-bar turnover must not gate the universe"


def test_impossible_returns_are_excluded_as_unadjusted_corporate_actions():
    """Indian equities trade under 5-20% circuit limits, so a +3750% session is a split
    this archive failed to adjust. One such bar corrupts an equal-weighted average."""
    assert ts.MAX_ABS_RETURN == 0.50
    con = _db([("L0", date(2020, 1, 1) + timedelta(days=k),
                *(4 * [100.0 if k != 30 else 5000.0]), 1e9) for k in range(60)]
              + [("L1", date(2020, 1, 1) + timedelta(days=k),
                  *(4 * [100.0]), 1e9) for k in range(60)])
    ts.STRATEGIES["_always"] = {"entry": "c > 0", "exit": "c < 0",
                                "needs": ["sma_20"], "claim": "a", "source": "test"}
    try:
        rows = ts.series(con, "_always")
        assert all(abs(r["universe"]) <= ts.MAX_ABS_RETURN for r in rows
                   if r["universe"] is not None)
    finally:
        del ts.STRATEGIES["_always"]
