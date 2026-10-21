"""ADR-0004: the replication gate, the turnover-matched null, and the trial budget."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import standard as S


def _series(mean_a, mean_b, n=60, sd=0.02, seed=0):
    rng = np.random.default_rng(seed)
    a = pd.Series(mean_a + sd * rng.standard_normal(n), index=pd.date_range("2016-01-31", periods=n, freq="ME"))
    b = pd.Series(mean_b + sd * rng.standard_normal(n), index=pd.date_range("2022-01-31", periods=n, freq="ME"))
    return pd.concat([a, b])


def test_replication_needs_both_halves():
    assert S.replication(_series(0.01, 0.01))["pass"]
    assert not S.replication(_series(0.01, 0.0))["pass"]          # one strong half is not replication
    assert S.replication(_series(-0.01, -0.01), direction=-1)["pass"]


def _panel(signal: float, seed=1):
    rng = np.random.default_rng(seed)
    dates = list(pd.date_range("2016-01-31", "2026-06-30", freq="ME"))
    names = [f"n{i}" for i in range(120)]
    F = pd.DataFrame(0.02 * rng.standard_normal((len(dates), len(names))), index=dates, columns=names)
    F[names[:20]] += signal                                        # the first 20 names carry the edge
    cost = pd.DataFrame(0.003, index=dates, columns=names)
    bench = F.mean(axis=1)
    books = {d: names[:20] for d in dates}
    eligible = {d: names for d in dates}
    return F, cost, bench, books, eligible


def test_null_separates_a_real_edge_and_not_noise():
    F, cost, bench, books, el = _panel(0.01)
    real = S.book_series(F, cost, bench, books)
    assert S.null_separation(real, F, cost, bench, books, el, draws=25)["pass"]
    F, cost, bench, books, el = _panel(0.0)
    real = S.book_series(F, cost, bench, books)
    assert not S.null_separation(real, F, cost, bench, books, el, draws=25)["pass"]


def test_random_books_match_size_and_turnover():
    names = [f"n{i}" for i in range(50)]
    dates = list(pd.date_range("2020-01-31", periods=6, freq="ME"))
    books = {d: names[i:i + 10] for i, d in enumerate(dates)}           # one name replaced each date
    rb = S.random_books(books, {d: names for d in dates}, seed=3)
    for a, b in zip(dates, dates[1:]):
        assert len(rb[b]) == 10
        assert len(set(rb[a]) & set(rb[b])) == len(set(books[a]) & set(books[b]))


def test_missing_future_return_does_not_remove_a_selected_holding():
    d = pd.Timestamp("2026-01-01")
    f = pd.DataFrame([[0.1, np.nan]], index=[d], columns=["A", "B"])
    c = pd.DataFrame(0.0, index=[d], columns=["A", "B"])
    with pytest.raises(ValueError, match="missing forward return"):
        S.book_series(f, c, pd.Series({d: 0.0}), {d: ["A", "B"]})


def test_dependence_audit_reduces_an_overlapping_signal_t_statistic():
    rng = np.random.default_rng(9)
    innovations = rng.normal(0, 1, 500)
    overlapping = pd.Series(0.05 + np.convolve(innovations, np.ones(10) / 10, mode="same"))
    audit = S.dependence_audit(overlapping, max_lag=9)
    assert audit["lag"] == 9
    assert abs(audit["t_hac"]) < abs(audit["t_iid"])


def test_budget_refuses_the_ninth_trial_and_a_repeat(tmp_path):
    p = tmp_path / "ledger.jsonl"
    for i in range(S.BUDGET_PER_QUARTER):
        S.record({"name": f"t{i}"}, p, on=date(2027, 4, 13))
    with pytest.raises(S.StandardError, match="budget"):
        S.record({"name": "t9"}, p, on=date(2027, 5, 1))
    S.record({"name": "next quarter"}, p, on=date(2027, 7, 5))
    with pytest.raises(S.StandardError, match="already"):
        S.record({"name": "next quarter"}, p, on=date(2027, 7, 6))
    S.record({"kind": "history", "name": "context"}, p, on=date(2027, 7, 6))   # history rows never count


def test_judge_without_a_null_is_not_a_candidate(tmp_path):
    r = S.judge("no such registration", _series(0.02, 0.02), repo=tmp_path)
    assert r["verdict"] == S.FAIL and "G1" in r["failed"] and "G4" in r["failed"]


def test_an_owner_override_raises_only_its_own_quarter(tmp_path):
    p = tmp_path / "ledger.jsonl"
    for i in range(S.BUDGET_OVERRIDES["2026Q4"]):
        S.record({"name": f"o{i}"}, p, on=date(2026, 11, 1))           # 2026Q4's owner-raised cap
    with pytest.raises(S.StandardError, match="budget"):
        S.record({"name": "one more"}, p, on=date(2026, 12, 1))
