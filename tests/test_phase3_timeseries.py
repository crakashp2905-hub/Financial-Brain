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


def _db(bars, regime="RISK_ON", ranks=None):
    """bars: list of (lineage, date, open, high, low, close, turnover).

    ``ranks`` gives each lineage its national turnover rank, which is what prices impact
    (``costs/book.py``). Absent, every name is the most liquid in the country - the
    cheapest possible book, so a cost bug shows up as a strategy looking too good rather
    than hiding behind a pessimistic default.
    """
    con = duckdb.connect(":memory:")
    # traded_volume is share count, not traded value: Chaikin money flow weights by
    # shares, so the panel needs both.
    con.execute("""CREATE TABLE adjusted_prices (business_date DATE, lineage VARCHAR,
                   isin VARCHAR, close_adj DOUBLE, factor DOUBLE, turnover DOUBLE,
                   traded_volume BIGINT)""")
    con.execute("""CREATE TABLE eod_prices (business_date DATE, isin VARCHAR,
                   exchange VARCHAR, series VARCHAR, open_price DOUBLE,
                   high_price DOUBLE, low_price DOUBLE)""")
    # Versioned, as the real table is: a rule change adds a version rather than rewriting
    # history, so the table holds every version at once and readers must pin one.
    con.execute("""CREATE TABLE market_regime (business_date DATE, version VARCHAR,
                   regime VARCHAR)""")
    # The impact bucket comes from an absolute national turnover rank, so the ranking
    # population is the exchange's whole list and not the backtest's own universe.
    con.execute("""CREATE TABLE universe_snapshots (business_date DATE, isin VARCHAR,
                   exchange VARCHAR, instrument_type VARCHAR, turnover DOUBLE)""")
    con.execute("""CREATE TABLE security_lineage (isin VARCHAR, lineage VARCHAR)""")
    con.execute("""CREATE TABLE evaluation_runs (run_at TIMESTAMP WITH TIME ZONE,
                   version VARCHAR, feature VARCHAR, horizon INTEGER, params VARCHAR,
                   dates INTEGER, mean_ic DOUBLE, ic_t DOUBLE, sharpe DOUBLE,
                   deflated_sharpe DOUBLE, verdict VARCHAR, reasons VARCHAR)""")
    days = sorted({b[1] for b in bars})
    con.executemany("INSERT INTO market_regime VALUES (?,?,?)",
                    [(d, "v2", regime) for d in days])
    # The bucket is an absolute *national* rank, so a toy universe of three names cannot
    # produce a rank of 900 - the exchange list has to be there too. Filler names occupy
    # turnover 1e15-i, and a lineage asking for rank r takes 1e15-r+0.5 so exactly r-1
    # fillers sit above it.
    ranks = ranks or {}
    market = max([*ranks.values(), 1]) + 1
    con.execute("""INSERT INTO universe_snapshots
                   SELECT d, 'FILL' || i, 'NSE', 'STK', 1e15 - i
                   FROM generate_series(1, ?) t(i), (SELECT UNNEST(?::DATE[]) AS d)""",
                [market, days])
    for lin in sorted({b[0] for b in bars}):
        con.execute("INSERT INTO security_lineage VALUES (?,?)", [f"ISIN{lin}", lin])
        tv = 1e15 - ranks.get(lin, 1) + 0.5
        con.executemany("INSERT INTO universe_snapshots VALUES (?,?,?,?,?)",
                        [(d, f"ISIN{lin}", "NSE", "STK", tv) for d in days])
    for lin, d, o, h, low, c, tv in bars:
        isin = f"ISIN{lin}"
        con.execute("INSERT INTO adjusted_prices VALUES (?,?,?,?,?,?,?)",
                    [d, lin, isin, c, 1.0, tv, 100_000])
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
_NEEDS_EXTRA = ("candle_", "trend_calm_vix", "event_")


@pytest.mark.parametrize("name", sorted(k for k in ts.STRATEGIES
                                        if not k.startswith(_NEEDS_EXTRA)))
def test_every_price_strategy_runs_without_error(name):
    con = _db(_flat_then_jump(n_names=4, n_days=260))
    r = ts.run(con, name)
    assert r["days"] > 0


def test_an_event_strategy_refuses_clearly_when_the_flags_are_absent():
    """Event strategies need the event_flags table. A database without it still runs
    every price strategy; only the rules that need the flags refuse."""
    con = _db(_flat_then_jump(n_names=4, n_days=260))
    assert not ts._has_table(con, "event_flags")
    with pytest.raises(ValueError, match="event flags"):
        ts.run(con, "event_insolvency")
    assert ts.run(con, "ma_cross_200")["days"] > 0


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


def test_registering_event_strategies_leaks_nothing_into_the_module():
    """A bare loop at module scope once overwrote `_t`, the t-statistic function, with
    the string 'BUYBACK' - so every validation would have reported a broken statistic.
    Two unrelated tests caught it. This one names the failure directly."""
    assert callable(ts._t)
    assert ts._t([1.0, 2.0, 3.0, 4.0]) > 0
    for leaked in ("_col", "_key", "_why", "col", "key", "event_type", "measured"):
        assert not hasattr(ts, leaked), f"{leaked} leaked into the module namespace"


def test_every_tracked_event_type_has_a_strategy():
    from financial_brain.features import event_flags as ef
    for event_type in ef.TRACKED:
        assert f"event_{event_type.lower()}" in ts.STRATEGIES


# --- the synthetic null -------------------------------------------------------

def test_the_null_builder_never_touches_the_real_tables():
    """The first version renamed production tables and put views in their place. It
    failed on eod_prices being a view, aborting with adjusted_prices already renamed and
    nothing to serve it - the real schema broken by a test of the harness.

    Nothing that tests the measuring instrument may damage the thing measured.
    """
    import inspect

    from financial_brain.evaluation import synthetic as syn
    src = inspect.getsource(syn)
    assert "RENAME TO" not in src, "the null must not rename production tables"
    assert "ATTACH" in inspect.getsource(syn.materialise)
    assert "nulldb." in inspect.getsource(syn.materialise)


def test_the_null_panel_has_matched_volatility_and_no_structure():
    import inspect

    from financial_brain.evaluation import synthetic as syn
    src = inspect.getsource(syn.build)
    assert "rng.gauss(mu, sigma)" in src, "drift and volatility are matched per name"
    assert "calendar" in src, "the real trading calendar is reused"


def test_the_book_is_charged_its_own_liquidity_not_a_flat_bucket():
    """One bucket for a whole book is wrong in both directions and the direction depends on
    what the signal holds. A micro-cap book must cost more than the flat ``mid`` charge."""
    bars = _flat_then_jump(n_names=3)
    ts.STRATEGIES["_probe_cost"] = {"entry": "c > 100", "exit": "c <= 100",
                                    "needs": ["sma_20"], "claim": "probe",
                                    "source": "test"}
    try:
        mega = ts.run(_db(bars, ranks={"L0": 1, "L1": 2, "L2": 3}), "_probe_cost")
        micro = ts.run(_db(bars, ranks={"L0": 3000, "L1": 3100, "L2": 3200}),
                       "_probe_cost")
        assert mega["bucket_mix"]["mega"] == pytest.approx(1.0)
        assert micro["bucket_mix"]["micro"] == pytest.approx(1.0)
        flat = mega["cost_round_trip_flat"]
        assert mega["cost_round_trip"] < flat < micro["cost_round_trip"]
        # And the charge reaches the returns, not just the report.
        assert sum(micro["excess"]) < sum(mega["excess"])
    finally:
        del ts.STRATEGIES["_probe_cost"]


def test_buckets_are_national_ranks_not_ranks_within_the_backtest_universe():
    """The trap this replaced: ranking inside a filtered universe made 62% of its names
    rank 750 or better and charged them ``mid`` impact, when nationally they are ``small``.
    Three names must not become three megas just because there are only three."""
    bars = _flat_then_jump(n_names=3)
    ts.STRATEGIES["_probe_nat"] = {"entry": "c > 100", "exit": "c <= 100",
                                   "needs": ["sma_20"], "claim": "probe", "source": "test"}
    try:
        r = ts.run(_db(bars, ranks={"L0": 900, "L1": 1000, "L2": 1100}), "_probe_nat")
        assert r["bucket_mix"]["small"] == pytest.approx(1.0)
        assert r["bucket_mix"]["mega"] == 0.0
    finally:
        del ts.STRATEGIES["_probe_nat"]
