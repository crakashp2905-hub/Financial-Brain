"""Geometric Brownian motion as a control: what every strategy scores on pure noise.

Brownian motion is not a strategy. It is the **null** - the thing prices would do if they
carried no information at all - and that makes it the most useful thing to run a strategy
library against.

The question this answers is not "does this strategy work" but the prior one: **would my
harness say it works even if nothing were there?** A strategy that scores well on paths
with no structure has found a bug, not an edge.

## Why this project needs it specifically

Four measurement artifacts have been found here in a week, every one of which produced a
confident, properly computed, entirely false number:

* a benchmark that returned **84% a year**, from a look-ahead hidden in a liquidity filter
  rather than in a signal;
* a spread estimator off by a factor of ten, because negative estimates were dropped
  instead of clamped;
* candlestick patterns at **t = 6** that were bid-ask bounce;
* a session median used as a benchmark nobody could buy.

Three of the four were caught by noticing an implausible number. That works, and it
depends on someone looking. A synthetic control does not: **run the strategy on noise, and
if it wins, the harness is wrong.** It is the one test that checks the measuring
instrument rather than the thing measured.

## What is matched, and what is destroyed

Each synthetic name is drawn with the **same drift and volatility** as a real one, and the
same number of sessions, so the panel has a realistic cross-section of volatilities rather
than one uniform noise level. Everything else is destroyed: no autocorrelation, no trend,
no mean reversion, no event clustering, no relationship between names.

A block-bootstrap of the real returns is offered as a second null. It preserves each
name's volatility *and* its fat tails and short-range dependence, which makes it the
harder control: a strategy that beats GBM but not the bootstrap is picking up
autocorrelation the bootstrap kept.

## The calibration this also gives

Running the full strategy library on noise gives the empirical distribution of the best
t-statistic obtainable from nothing. If 78 strategies on noise routinely produce a maximum
|t| near 3.4, then the Bonferroni bar of 3.4 is calibrated. If noise routinely beats it,
the bar is too low and every past rejection was generous rather than harsh.

That is a direct empirical check on the firewall itself, which has never been tested
against a known-null world.
"""
from __future__ import annotations

import math
import random

VERSION = "syn1"
DEFAULT_SEED = 20260926


def moments(con, min_sessions: int = 500, limit: int = 200) -> list[dict]:
    """Per-name drift and volatility of log returns, from the real panel."""
    rows = con.execute("""
        WITH r AS (
            SELECT lineage, business_date,
                   LN(close_adj / NULLIF(LAG(close_adj) OVER
                       (PARTITION BY lineage ORDER BY business_date), 0)) AS lr,
                   turnover
            FROM adjusted_prices WHERE close_adj > 0
        )
        SELECT lineage, AVG(lr), STDDEV_SAMP(lr), COUNT(*), AVG(turnover),
               MIN(business_date), MAX(business_date)
        FROM r WHERE lr IS NOT NULL AND ABS(lr) < 0.5
        GROUP BY 1 HAVING COUNT(*) >= ?
        ORDER BY AVG(turnover) DESC LIMIT ?""", [min_sessions, limit]).fetchall()
    return [{"lineage": r[0], "mu": r[1], "sigma": r[2], "n": r[3], "adv": r[4],
             "start": r[5], "end": r[6]} for r in rows]


def build(con, *, seed: int = DEFAULT_SEED, min_sessions: int = 500,
          limit: int = 200, bootstrap: bool = False) -> dict:
    """Write a synthetic panel with the same shape as the real one and no structure.

    The calendar is the *real* trading calendar, so anything the harness does with dates -
    the next-session mapping, month ends, the regime join - behaves identically. Only the
    returns are noise.
    """
    rng = random.Random(seed)
    names = moments(con, min_sessions=min_sessions, limit=limit)
    if not names:
        return {"names": 0, "rows": 0}

    calendar = [r[0] for r in con.execute(
        "SELECT DISTINCT business_date FROM adjusted_prices ORDER BY 1").fetchall()]

    pool: list[float] = []
    if bootstrap:
        pool = [r[0] for r in con.execute("""
            SELECT LN(close_adj / NULLIF(LAG(close_adj) OVER
                     (PARTITION BY lineage ORDER BY business_date), 0)) AS lr
            FROM adjusted_prices WHERE close_adj > 0
            QUALIFY lr IS NOT NULL AND ABS(lr) < 0.5
            USING SAMPLE 200000 ROWS""").fetchall()]

    con.execute("""CREATE OR REPLACE TABLE synthetic_prices (
        business_date DATE, lineage VARCHAR, isin VARCHAR, close_adj DOUBLE,
        factor DOUBLE, turnover DOUBLE, traded_volume BIGINT)""")

    # Bulk-load through a CSV staging file. executemany binds row by row at ~1.2 ms, and
    # 200 names of 2,600 sessions is half a million rows - ten minutes of binding against
    # thirty seconds of generating. The same trick the announcements and minute-bar
    # ingests use, for the same reason.
    import csv
    import tempfile
    from pathlib import Path

    n_rows = 0
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="",
                                     encoding="utf-8") as fh:
        staging = Path(fh.name)
        w = csv.writer(fh)
        for i, nm in enumerate(names):
            lineage = f"SYN{i:04d}"
            price = 100.0
            mu, sigma = nm["mu"] or 0.0, nm["sigma"] or 0.01
            span = calendar[-nm["n"]:] if nm["n"] <= len(calendar) else calendar
            for d in span:
                step = rng.choice(pool) if pool else rng.gauss(mu, sigma)
                price *= math.exp(step)
                w.writerow([d.isoformat(), lineage, f"{lineage}IN", f"{price:.6f}",
                            1.0, nm["adv"] or 1e8, 1000])
                n_rows += 1
    try:
        con.execute(f"""INSERT INTO synthetic_prices
            SELECT * FROM read_csv('{staging.as_posix()}', header=false, columns={{
                'business_date': 'DATE', 'lineage': 'VARCHAR', 'isin': 'VARCHAR',
                'close_adj': 'DOUBLE', 'factor': 'DOUBLE', 'turnover': 'DOUBLE',
                'traded_volume': 'BIGINT'}})""")
    finally:
        staging.unlink(missing_ok=True)
    rows = range(n_rows)
    return {"version": VERSION, "names": len(names), "rows": len(rows),
            "seed": seed, "bootstrap": bootstrap,
            "mean_sigma": sum(n["sigma"] or 0 for n in names) / len(names)}


def materialise(con, path) -> str:
    """Write a standalone null database the strategies can be pointed at.

    The first version of this renamed the production tables and put views in their place.
    That was a bad design and it failed in the obvious way: ``eod_prices`` is a view, the
    rename raised, and the run aborted with ``adjusted_prices`` already renamed and no
    replacement - leaving the real schema broken until it was restored by hand.

    Nothing that tests the harness should be able to damage the thing under test. This
    builds a *separate database file* with the same table names and shapes, so the
    strategies run unmodified against a schema they recognise while the real one is only
    ever read.
    """
    con.execute(f"ATTACH '{path}' AS nulldb")
    try:
        con.execute("""CREATE OR REPLACE TABLE nulldb.adjusted_prices AS
            SELECT business_date, lineage, isin, close_adj, factor, turnover,
                   traded_volume, close_adj AS close_raw, lineage AS ticker
            FROM synthetic_prices""")
        # Synthetic OHLC is a flat bar at the close. A strategy that needs a real
        # intraday range gets no range here, which is the honest treatment: the null has
        # no microstructure to imitate.
        con.execute("""CREATE OR REPLACE TABLE nulldb.eod_prices AS
            SELECT business_date, business_date AS trade_date, isin,
                   'NSE' AS exchange, 'EQ' AS series,
                   close_adj AS open_price, close_adj AS high_price,
                   close_adj AS low_price, close_adj AS close_price, turnover
            FROM synthetic_prices""")
        con.execute("""CREATE OR REPLACE TABLE nulldb.market_regime AS
            SELECT DISTINCT business_date, 'NEUTRAL' AS regime FROM synthetic_prices""")
        con.execute("""CREATE OR REPLACE TABLE nulldb.evaluation_runs (
            run_at TIMESTAMP WITH TIME ZONE, version VARCHAR, feature VARCHAR,
            horizon INTEGER, params VARCHAR, dates INTEGER, mean_ic DOUBLE,
            ic_t DOUBLE, sharpe DOUBLE, deflated_sharpe DOUBLE, verdict VARCHAR,
            reasons VARCHAR)""")
    finally:
        con.execute("DETACH nulldb")
    return str(path)
