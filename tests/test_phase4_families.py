"""Research families, and the discipline that stops a correction becoming a manipulation.

This module lowers a bar, which is the single most suspect thing a statistics module can do - and it
was written immediately after a result that needed a lower one. So the tests are mostly about the
guards: that the correction penalises the search as well as relaxing the bar, that a taxonomy
declared after a result cannot promote it, and that the two halves cannot be taken separately.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import duckdb
import pytest

from financial_brain.evaluation import families as F


def _db(trials):
    """trials: list of (feature, horizon, ic_t) or (feature, horizon, ic_t, ic_series, dates)."""
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE evaluation_runs (run_at TIMESTAMP, version VARCHAR,
                   feature VARCHAR, horizon INTEGER, params VARCHAR, dates INTEGER,
                   mean_ic DOUBLE, ic_t DOUBLE, sharpe DOUBLE, deflated_sharpe DOUBLE,
                   verdict VARCHAR, reasons VARCHAR, ic_series DOUBLE[],
                   ic_dates DATE[])""")
    for t in trials:
        feature, horizon, ict = t[0], t[1], t[2]
        series = t[3] if len(t) > 3 else None
        dates = t[4] if len(t) > 4 else None
        con.execute("""INSERT INTO evaluation_runs VALUES
                       (NULL,'fw1',?,?,'{}',100,0.01,?,0.1,0.5,'REJECT','[]',?,?)""",
                    [feature, horizon, ict, series, dates])
    return con


# ------------------------------------------------------------------------ the taxonomy
def test_signals_making_one_claim_are_one_family():
    """George & Hwang (2004) argue the 52-week high *subsumes* momentum. Splitting them to get
    three families would be exactly the manipulation this module exists to prevent."""
    for f in ("mom_12_1", "dist_52w_high", "above_ma200", "faber_taa_10m", "turtle_20_10"):
        assert F.family_of(f) == "momentum", f
    for f in ("ret_20d", "rsi_14", "bollinger_reversion_20_2"):
        assert F.family_of(f) == "reversal", f
    for f in ("vol_60", "vol_20", "atr_14_pct"):
        assert F.family_of(f) == "volatility", f


def test_an_unlisted_signal_is_its_own_family_rather_than_assumed_into_one():
    """An unclassified signal is not evidence of independence, but assuming it belongs somewhere is
    a judgement the taxonomy has not earned."""
    assert F.family_of("some_new_signal") == F.UNASSIGNED


def test_event_and_candle_strategies_are_matched_by_prefix():
    assert F.family_of("event_insolvency") == "events"
    assert F.family_of("candle_hammer_hold20") == "candles"


# ----------------------------------------------------------- the correction cuts both ways
def test_the_family_bar_is_lower_than_the_trial_bar():
    con = _db([("mom_12_1", h, 3.0) for h in range(1, 40)])
    c = F.census(con)
    assert c["bar_by_family"] < c["bar_by_trial"]


def test_the_search_penalty_grows_with_the_number_of_variants_tried():
    assert F.search_penalty(1) == 0.0
    assert F.search_penalty(16) == pytest.approx(math.sqrt(2 * math.log(16)))
    assert F.search_penalty(69) > F.search_penalty(16) > F.search_penalty(4)


def test_a_heavily_searched_family_is_pushed_further_from_passing_not_closer():
    """The result that makes this module trustworthy. On the real ledger the momentum family has
    spent 69 of 150 trials, so its search penalty of 2.91 t units exceeds the 0.86 the bar falls
    by - family framing moves the candidate AWAY from a pass."""
    con = _db([("mom_12_1", h, 3.0) for h in range(1, 70)]
              + [("vol_60", 20, 2.0), ("ret_20d", 20, 2.0)])
    c = F.census(con)
    bar_drop = c["bar_by_trial"] - c["bar_by_family"]
    momentum = next(d for d in c["by_family"] if d["family"] == "momentum")
    assert momentum["search_penalty_upper"] > bar_drop, (
        f"penalty {momentum['search_penalty_upper']:.2f} should exceed the bar drop "
        f"{bar_drop:.2f}")


def test_the_lower_bar_cannot_be_taken_without_the_penalty():
    con = _db([("mom_12_1", h, 3.0) for h in range(1, 70)])
    v = F.verdict(con, feature="mom_12_1", statistic=3.2,
                  run_on=F.DECLARED_AFTER + timedelta(days=1))
    # 3.2 beats the family bar on its own, and must not once the search is charged for.
    assert v["statistic"] > v["bar_by_family"]
    assert v["statistic_after_search_penalty"] < v["bar_by_family"]
    assert v["clears_family_bar_after_penalty"] is False
    assert v["verdict"] == "REJECT"


# --------------------------------------------------------------- the eligibility guard
def test_a_taxonomy_declared_after_a_result_cannot_promote_it():
    """A bar chosen with knowledge of the statistics it will judge is not a bar.

    Constructed so eligibility is the *only* thing in the way: one momentum trial, so its search
    penalty is zero, and a statistic that sits between the family bar and the trial bar. Any other
    arrangement would let a different guard take the credit.
    """
    con = _db([("mom_12_1", 20, 2.0)]
              + [("vol_60", h, 2.0) for h in range(1, 20)]
              + [("ret_20d", h, 2.0) for h in range(1, 20)])
    c = F.census(con)
    stat = (c["bar_by_trial"] + c["bar_by_family"]) / 2
    assert c["bar_by_family"] < stat < c["bar_by_trial"]

    before = F.verdict(con, feature="mom_12_1", statistic=stat,
                       run_on=F.DECLARED_AFTER - timedelta(days=1))
    assert before["search_penalty_upper"] == 0.0        # one variant, nothing searched
    assert before["clears_family_bar_after_penalty"] is True
    assert before["family_bar_eligible"] is False
    assert before["verdict"] == "REJECT", "only eligibility should be blocking this"
    assert "already visible" in before["why"]

    after = F.verdict(con, feature="mom_12_1", statistic=stat,
                      run_on=F.DECLARED_AFTER + timedelta(days=1))
    assert after["family_bar_eligible"] is True
    assert after["verdict"] == "PASS_ON_FAMILY_BAR"


def test_clearing_the_trial_bar_needs_no_argument_about_families():
    con = _db([("mom_12_1", h, 3.0) for h in range(1, 40)])
    v = F.verdict(con, feature="mom_12_1", statistic=9.9,
                  run_on=F.DECLARED_AFTER - timedelta(days=10))
    assert v["clears_trial_bar"] is True
    assert v["verdict"] == "PASS"
    assert "needs no argument" in v["why"]


# --------------------------------------------------------------- the effective count
def test_the_effective_count_is_bounded_when_no_series_is_stored():
    """The ledger's first 150 trials predate the ic_series column, so the effective count can only
    be bounded to [families, trials] - and an interval that wide decides nothing."""
    con = _db([("mom_12_1", 20, 3.0), ("vol_60", 20, 2.0), ("ret_20d", 20, 2.0)])
    c = F.census(con)
    assert c["effective_trials_lower"] == c["families"]
    assert c["effective_trials_upper"] == c["trials"]
    r = F.effective_tests(con)
    assert r["effective_tests"] is None
    assert "before the series was stored" in r["why"]


def test_the_effective_count_recovers_the_number_of_distinct_ideas():
    """Six trials that are really two ideas must come back near two, not six."""
    import random

    rng = random.Random(4)
    n = 60
    dates = [date(2024, 1, 1) + timedelta(days=k) for k in range(n)]
    a = [rng.gauss(0, 1) for _ in range(n)]
    b = [rng.gauss(0, 1) for _ in range(n)]
    trials = []
    for i in range(3):
        trials.append(("mom_12_1", 20 + i,
                       3.0, [x + rng.gauss(0, 0.05) for x in a], dates))
    for i in range(3):
        trials.append(("vol_60", 20 + i,
                       2.0, [x + rng.gauss(0, 0.05) for x in b], dates))
    con = _db(trials)
    r = F.effective_tests(con)
    assert r["effective_tests"] == pytest.approx(2.0, abs=0.4), r
    assert r["trials_with_series"] == 6
    assert r["bar_at_effective"] < F.bonferroni(7)


def test_unaligned_series_are_refused_rather_than_correlated():
    """Two trials at different horizons rebalance on different days; correlating unaligned series
    would compare different weeks."""
    d1 = [date(2024, 1, 1) + timedelta(days=k) for k in range(30)]
    d2 = [date(2025, 1, 1) + timedelta(days=k) for k in range(30)]
    con = _db([("mom_12_1", 20, 3.0, [0.01] * 30, d1),
               ("mom_12_1", 40, 3.0, [0.01] * 30, d2),
               ("vol_60", 20, 2.0, [0.02] * 30, d2)])
    r = F.effective_tests(con)
    assert r["effective_tests"] is None
    assert "shared by all" in r["why"]


def test_a_census_on_an_empty_ledger_does_not_raise():
    con = _db([])
    c = F.census(con)
    assert c["trials"] == 0 and c["families"] == 0
    assert c["bar_by_trial"] > 0
