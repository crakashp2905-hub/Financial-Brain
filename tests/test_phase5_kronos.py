"""Adjusted bars, and the Kronos adapter exercised without torch or 100MB of weights.

Everything the adapter is responsible for can be tested against a stub predictor: the context window,
the horizon cap, the point-in-time boundary, the refusal to fake candles from closes, and the one
failure that would be catastrophic and silent - reporting a single returned frame as a distribution of
n identical paths, which has zero spread and therefore scores as a flawless forecast.

The bars loader gets the same attention, because both of its joins are wrong in ways that do not
raise: eod_prices holds two exchanges' bars per session, and the raw fields are unadjusted while the
close is adjusted.
"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from financial_brain.forecasting import bars as B
from financial_brain.forecasting import kronos as K
from financial_brain.forecasting import walkforward as W
from financial_brain.forecasting.distribution import ForecastError

START = date(2021, 1, 4)


def _db(*, sessions=400, names=3, factor=1.0):
    """Built with generate_series: row-by-row inserts of 400 sessions x 3 names x 2 exchanges ran
    this file in 231 seconds, which is a fact this project has already learned once."""
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE adjusted_prices (business_date DATE, isin VARCHAR,
                   lineage VARCHAR, close_raw DOUBLE, close_adj DOUBLE, factor DOUBLE,
                   turnover DOUBLE, traded_volume BIGINT)""")
    con.execute("""CREATE TABLE eod_prices (business_date DATE, isin VARCHAR, series VARCHAR,
                   exchange VARCHAR, open_price DOUBLE, high_price DOUBLE, low_price DOUBLE,
                   close_price DOUBLE, traded_volume BIGINT, turnover DOUBLE)""")
    con.execute("CREATE TABLE security_lineage (isin VARCHAR, lineage VARCHAR)")
    con.execute("CREATE TABLE features (business_date DATE, lineage VARCHAR, adv20 DOUBLE)")
    con.execute("""CREATE TABLE evaluation_runs (run_at TIMESTAMP, version VARCHAR,
                   feature VARCHAR, horizon INTEGER, params VARCHAR, dates INTEGER,
                   mean_ic DOUBLE, ic_t DOUBLE, sharpe DOUBLE, deflated_sharpe DOUBLE,
                   verdict VARCHAR, reasons VARCHAR, ic_series DOUBLE[], ic_dates DATE[])""")
    con.execute("""
        CREATE TEMP TABLE raw AS
        SELECT CAST(? AS DATE) + CAST(s.k AS INTEGER) AS business_date,
               'INE' || LPAD(CAST(n.i AS VARCHAR), 9, '0') AS isin,
               'L' || CAST(n.i AS VARCHAR) AS lineage,
               100.0 * POWER(1.0004, s.k) * (1 + 0.01 * (((s.k + n.i) % 7) - 3)) AS raw,
               ? AS factor
        FROM generate_series(0, ? - 1) AS s(k), generate_series(0, ? - 1) AS n(i)
    """, [START, factor, sessions, names])
    con.execute("""INSERT INTO adjusted_prices
                   SELECT business_date, isin, lineage, raw, raw * factor, factor,
                          1e9, 100000 FROM raw""")
    con.execute("""INSERT INTO security_lineage SELECT DISTINCT isin, lineage FROM raw""")
    con.execute("""INSERT INTO features
                   SELECT business_date, lineage, 1e9 FROM raw""")
    # NSE EQ, the series adjusted_prices is built from...
    con.execute("""INSERT INTO eod_prices
                   SELECT business_date, isin, 'EQ', 'NSE', raw * 0.99, raw * 1.02,
                          raw * 0.98, raw, 100000, 1e9 FROM raw""")
    # ...and a BSE bar for the same session, 9% lower, which must never appear in a loaded history.
    con.execute("""INSERT INTO eod_prices
                   SELECT business_date, isin, 'B', 'BSE', raw * 0.90, raw * 0.93,
                          raw * 0.88, raw * 0.91, 5000, 5e7 FROM raw""")
    return con


# ------------------------------------------------------------------------------ the bars
def test_only_the_series_the_adjusted_close_was_built_from_is_loaded():
    """The join that returns two exchanges interleaved. On real data 44% of the joined rows for 2025
    onward had a close disagreeing with the adjusted close, which is a series that alternates between
    NSE and BSE - and nothing raises."""
    con = _db(sessions=200)
    got = B.history(con, "L0", START + timedelta(days=199))
    assert got
    for bar in got:
        adj = con.execute("""SELECT close_adj FROM adjusted_prices
                             WHERE lineage = ? AND business_date = ?""",
                          ["L0", bar.session]).fetchone()[0]
        assert bar.close == pytest.approx(adj), (
            f"{bar.session}: close {bar.close} is not the adjusted close {adj}")
        # The BSE bar sits 9% lower; if it leaked in, open would be near 0.90 of the close.
        assert bar.open / bar.close == pytest.approx(0.99, abs=1e-6)


def test_every_price_field_carries_the_same_adjustment_as_the_close():
    """close_adj = close_raw * factor, with factors down to 0.00133 in the real database, so a raw
    OHLC series reads a 1:10 split as a 90% single-session crash."""
    con = _db(sessions=200, factor=0.2)
    got = B.history(con, "L0", START + timedelta(days=199))
    assert got
    for bar in got:
        raw = con.execute("""SELECT close_raw, close_adj FROM adjusted_prices
                             WHERE lineage = ? AND business_date = ?""",
                          ["L0", bar.session]).fetchone()
        assert bar.close == pytest.approx(raw[0] * 0.2)
        assert bar.high == pytest.approx(raw[0] * 1.02 * 0.2)
        assert bar.low == pytest.approx(raw[0] * 0.98 * 0.2)
        # Volume moves the other way: a split multiplies the share count by what it divides price by.
        assert bar.volume == pytest.approx(100000 / 0.2)


def test_a_bar_whose_own_arithmetic_fails_is_dropped_rather_than_repaired():
    """A high below the close is two sources glued together, or an adjustment applied to some fields
    and not others. Repairing it would be a guess about which field was missed."""
    con = _db(sessions=200)
    bad = START + timedelta(days=100)
    con.execute("""UPDATE eod_prices SET high_price = close_price * 0.5
                   WHERE isin = ? AND business_date = ? AND exchange = 'NSE'""",
                ["INE000000000", bad])
    got = B.history(con, "L0", START + timedelta(days=199))
    assert bad not in {b.session for b in got}
    assert all(b.is_sane() for b in got)


def test_bars_stop_at_the_as_of_session():
    con = _db(sessions=200)
    as_of = START + timedelta(days=120)
    got = B.history(con, "L0", as_of)
    assert got[-1].session == as_of
    assert all(b.session <= as_of for b in got)


def test_bars_come_back_oldest_first():
    con = _db(sessions=200)
    got = B.history(con, "L0", START + timedelta(days=199))
    assert [b.session for b in got] == sorted(b.session for b in got)


# -------------------------------------------------------------------------- the adapter
class _Stub:
    """A stand-in for KronosPredictor, mirroring how the real one aggregates.

    The real ``predict`` averages its samples and returns ONE frame; ``predict_batch`` keeps series
    separate. So this stub returns one frame from ``predict`` regardless of ``sample_count``, and one
    frame per series from ``predict_batch`` - which is what the adapter has to cope with.
    """

    def __init__(self, *, samples=None, steps=None, single_frame=False):
        self.samples, self.steps, self.single_frame = samples, steps, single_frame
        self.calls: list[dict] = []

    @staticmethod
    def _closes(df):
        """Works for a pandas frame and for the plain column dict used without pandas."""
        col = df["close"]
        return [float(v) for v in (col.tolist() if hasattr(col, "tolist") else col)]

    def predict(self, *, df, x_timestamp, y_timestamp, pred_len, T, top_p,
                sample_count, verbose=False):
        closes = self._closes(df)
        self.calls.append({"rows": len(closes), "pred_len": pred_len, "T": T,
                           "top_p": top_p, "sample_count": sample_count,
                           "last_close": closes[-1]})
        steps = self.steps or pred_len
        base = closes[-1]
        # One frame, averaged - exactly what the real predict() does.
        return [base * (1 + 0.001 * (j + 1)) for j in range(steps)]

    def predict_batch(self, *, df_list, x_timestamp_list, y_timestamp_list, pred_len,
                      T, top_p, sample_count, verbose=False):
        closes = self._closes(df_list[0])
        self.calls.append({"rows": len(closes), "pred_len": pred_len, "T": T,
                           "top_p": top_p, "sample_count": sample_count,
                           "series": len(df_list), "last_close": closes[-1]})
        steps = self.steps or pred_len
        n = 1 if self.single_frame else (self.samples or len(df_list))
        base = closes[-1]
        return [[base * (1 + 0.001 * (j + 1) * (i + 1)) for j in range(steps)]
                for i in range(n)]


class _StubNoBatch(_Stub):
    """Only ``predict``, as an older checkpoint exposes. The adapter must still work - with one
    path, since one averaged frame is one path."""

    predict_batch = None

    def __getattribute__(self, item):
        if item == "predict_batch":
            raise AttributeError(item)
        return super().__getattribute__(item)


def _forecaster(**kw):
    f = K.KronosForecaster(**kw)
    return f


def test_a_close_only_history_is_refused_rather_than_padded_into_flat_candles():
    """A model trained on K-lines given open == high == low == close is being fed a distribution it
    never saw."""
    f = _forecaster()
    with pytest.raises(ForecastError, match="close-only"):
        f.forecast(instrument="INE0", as_of=START, horizon=10,
                   prices=[100.0] * 300, n_paths=10)


def test_a_horizon_that_asks_the_model_to_write_more_than_it_read_is_refused():
    con = _db(sessions=400)
    window = B.history(con, "L0", START + timedelta(days=399))
    f = _forecaster(context=200)
    f._predictor = _Stub()
    with pytest.raises(ForecastError, match="cap is"):
        f.forecast(instrument="INE0", as_of=START, horizon=120, bars=window, n_paths=5)
    out = f.forecast(instrument="INE0", as_of=START, horizon=20, bars=window, n_paths=5)
    assert out.horizon == 20


def test_too_little_context_is_refused():
    con = _db(sessions=200)
    window = B.history(con, "L0", START + timedelta(days=60))
    f = _forecaster()
    f._predictor = _Stub()
    with pytest.raises(ForecastError, match="below"):
        f.forecast(instrument="INE0", as_of=START, horizon=10, bars=window, n_paths=5)


def test_the_model_is_given_the_last_context_bars_and_no_more():
    con = _db(sessions=400)
    window = B.history(con, "L0", START + timedelta(days=399))
    stub = _Stub()
    f = _forecaster(context=250)
    f._predictor = stub
    f.forecast(instrument="INE0", as_of=START + timedelta(days=399), horizon=20,
               bars=window, n_paths=7)
    assert stub.calls[0]["rows"] == 250
    assert stub.calls[0]["last_close"] == pytest.approx(window[-1].close)
    # Independent draws come from repeating the series, not from sample_count, which Kronos averages.
    assert stub.calls[0]["series"] == 7
    assert stub.calls[0]["sample_count"] == 1


def test_the_anchor_is_the_last_context_close_and_paths_exclude_it():
    con = _db(sessions=400)
    window = B.history(con, "L0", START + timedelta(days=399))
    f = _forecaster()
    f._predictor = _Stub()
    out = f.forecast(instrument="INE0", as_of=START + timedelta(days=399), horizon=15,
                     bars=window, n_paths=6)
    assert out.anchor == pytest.approx(window[-1].close)
    assert out.n_paths == 6
    assert all(len(p) == 15 for p in out.paths)
    assert out.meta["last_context_session"] == window[-1].session


def test_sample_count_is_not_how_a_distribution_is_drawn():
    """The finding that decides whether this adapter produces a distribution at all.

    Kronos' ``auto_regressive_inference`` runs sample_count draws in parallel and finishes with
    ``np.mean(preds, axis=1)``, so ``predict(sample_count=30)`` returns ONE mean path. The adapter
    has to get its spread from repeating the series through ``predict_batch`` with sample_count=1,
    because predict_batch averages within a series and keeps series apart.
    """
    con = _db(sessions=400)
    window = B.history(con, "L0", START + timedelta(days=399))
    stub = _Stub()
    f = _forecaster()
    f._predictor = stub
    out = f.forecast(instrument="INE0", as_of=START + timedelta(days=399), horizon=10,
                     bars=window, n_paths=25)
    assert out.n_paths == 25
    assert stub.calls[0]["sample_count"] == 1, "asking Kronos for 25 samples returns their mean"
    assert stub.calls[0]["series"] == 25
    # And the paths are genuinely different, so the distribution has spread.
    assert len({tuple(p) for p in out.paths}) == 25


def test_a_predictor_without_the_batch_api_yields_one_path_rather_than_a_fake_distribution():
    con = _db(sessions=400)
    window = B.history(con, "L0", START + timedelta(days=399))
    f = _forecaster()
    f._predictor = _StubNoBatch()
    out = f.forecast(instrument="INE0", as_of=START + timedelta(days=399), horizon=10,
                     bars=window, n_paths=25)
    assert out.n_paths == 1, "one averaged frame is one path, not 25"
    assert out.quantiles()["p05"] is None


def test_one_returned_frame_is_one_path_and_is_never_duplicated_into_a_distribution():
    """The most dangerous possible failure in this adapter. n identical paths have zero spread, which
    gives zero-width intervals, which score as a perfectly sharp and perfectly calibrated forecast."""
    con = _db(sessions=400)
    window = B.history(con, "L0", START + timedelta(days=399))
    f = _forecaster()
    f._predictor = _Stub(single_frame=True)
    out = f.forecast(instrument="INE0", as_of=START + timedelta(days=399), horizon=10,
                     bars=window, n_paths=50)
    assert out.n_paths == 1, "a single frame must not be inflated to the requested sample count"
    # And a one-path distribution reports no tails, so it cannot be scored as a sharp forecast.
    assert out.quantiles()["p05"] is None
    with pytest.raises(ForecastError):
        from financial_brain.forecasting import calibration as C
        C.crps(out.terminal(), window[-1].close)


def test_a_frame_of_the_wrong_length_is_refused_rather_than_trimmed():
    con = _db(sessions=400)
    window = B.history(con, "L0", START + timedelta(days=399))
    f = _forecaster()
    f._predictor = _Stub(steps=7)
    with pytest.raises(ForecastError, match="steps"):
        f.forecast(instrument="INE0", as_of=START, horizon=20, bars=window, n_paths=5)


def test_the_sampling_configuration_is_recorded_with_the_forecast():
    """A temperature that happened to work is a searched parameter, and the ledger has to know it
    was spent."""
    con = _db(sessions=400)
    window = B.history(con, "L0", START + timedelta(days=399))
    f = _forecaster(temperature=0.7, top_p=0.85, seed=11)
    f._predictor = _Stub()
    out = f.forecast(instrument="INE0", as_of=START + timedelta(days=399), horizon=10,
                     bars=window, n_paths=5)
    assert out.meta["temperature"] == 0.7
    assert out.meta["top_p"] == 0.85
    assert out.meta["seed"] == 11
    assert out.model == "kronos-small"
    assert out.model_version == K.CHECKPOINTS["kronos-small"][0]


def test_an_impossible_configuration_is_refused_at_construction():
    with pytest.raises(ForecastError, match="unknown checkpoint"):
        K.KronosForecaster(checkpoint="kronos-enormous")
    with pytest.raises(ForecastError, match="top_p"):
        K.KronosForecaster(top_p=1.5)
    with pytest.raises(ForecastError, match="temperature"):
        K.KronosForecaster(temperature=0.0)


def test_missing_weights_are_a_distinct_failure_from_losing_to_the_null():
    """"Not evaluated" and "did not beat the bootstrap" are different findings, and collapsing them
    is how an untested model acquires a verdict."""
    assert issubclass(K.KronosUnavailable, ForecastError)

    class Broken(K.KronosForecaster):
        def _load(self):
            raise K.KronosUnavailable("weights absent")

    f = Broken()
    assert f.available is False, "availability is reported, not raised"
    con = _db(sessions=200)
    window = B.history(con, "L0", START + timedelta(days=199))
    with pytest.raises(K.KronosUnavailable):
        f.forecast(instrument="INE0", as_of=START, horizon=20, bars=window, n_paths=5)


# ------------------------------------------------------------------- through the harness
def test_a_bar_hungry_forecaster_is_walked_on_bars_and_the_nulls_on_those_same_closes():
    """If the candidate reads bars and the nulls read a separately-queried close history, a bar
    dropped for failing its own arithmetic leaves the two seeing different pasts."""
    con = _db(sessions=400, names=6)

    class BarStub:
        name = "barstub"
        needs_bars = True

        def __init__(self):
            self.seen_lengths: list[int] = []

        def forecast(self, *, instrument, as_of, horizon, bars=None, prices=None,
                     n_paths=10):
            assert bars is not None, "the harness must hand bars to a needs_bars forecaster"
            assert prices is None or prices == []
            self.seen_lengths.append(len(bars))
            from financial_brain.forecasting.distribution import ForecastDistribution
            anchor = bars[-1].close
            return ForecastDistribution(
                instrument=instrument, as_of=as_of, horizon=horizon, anchor=anchor,
                paths=[[anchor * (1 + 0.001 * i)] * horizon for i in range(n_paths)],
                model=self.name)

    stub = BarStub()
    study = W.walk(con, stub, start=START, end=START + timedelta(days=399), horizon=20,
                   names=6, n_paths=20)
    assert study.records
    assert stub.seen_lengths
    assert study.params["context"] == "bars"
    for name, recs in study.null_records.items():
        assert len(recs) == len(study.records), f"{name} saw a different sample"
    for (cand, _), (ref, _) in zip(study.records, study.null_records["climatology"]):
        assert cand.anchor == pytest.approx(ref.anchor), (
            "the candidate and the null must be anchored on the same last price")


def test_a_history_spans_an_isin_succession_because_an_isin_is_not_a_company():
    """The bug that would have fed a foundation model a series ending seven years early.

    785 of this database's 16,217 lineages span more than one ISIN. Keying on the ISIN truncates the
    history at the succession and nothing raises: the real case had 1,166 sessions ending 2019-09-19
    where the lineage has 2,903 ending 2026. Here one lineage changes ISIN halfway.
    """
    con = _db(sessions=400)
    sessions = [r[0] for r in con.execute(
        "SELECT DISTINCT business_date FROM adjusted_prices ORDER BY 1").fetchall()]
    cut = sessions[200]
    for table in ("adjusted_prices", "eod_prices"):
        con.execute(f"""UPDATE {table} SET isin = 'INE999999999'
                        WHERE isin = 'INE000000000' AND business_date >= ?""", [cut])
    con.execute("INSERT INTO security_lineage VALUES ('INE999999999', 'L0')")

    whole = B.history(con, "L0", sessions[-1], 750)
    assert len(whole) == 400, f"the lineage lost {400 - len(whole)} sessions at the succession"
    assert whole[-1].session == sessions[-1]
    assert {s.session for s in whole} == set(sessions)

    # And the realised price is found across the succession rather than coming back None.
    assert W.realised(con, "L0", sessions[150], 20) is not None
