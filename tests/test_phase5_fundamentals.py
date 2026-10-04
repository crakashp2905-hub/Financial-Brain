import numpy as np
import pandas as pd
import pytest

from financial_brain.features import fundamentals as F


def quarters(n=10, eps=None, rev=100.0, pat=10.0, start="2019-03-31"):
    ends = pd.date_range(start, periods=n, freq="QE")
    eps = eps if eps is not None else [1.0] * n
    df = pd.DataFrame({"period_end": ends, "filed_at": ends + pd.Timedelta(days=40), "basis": "consolidated",
                       "isin": "I", "revenue": rev, "pat": pat, "eps": eps})
    return df


def newest_first(df):
    return df.sort_values("period_end", ascending=False).reset_index(drop=True)


def test_margin_and_growth_from_consecutive_quarters():
    q = newest_first(quarters(8, rev=[100] * 4 + [120] * 4, pat=12.0))
    f = F.factors_for(q, price=100.0, shares=10.0)
    assert f["margin"] == pytest.approx(48 / 480) and f["rev_growth"] == pytest.approx(480 / 400 - 1)
    assert f["ep"] == pytest.approx(48 / (100 * 10))


def test_a_hole_in_the_quarters_gives_nothing_not_a_wrong_window():
    df = quarters(8)
    df = df.drop(index=[6]).reset_index(drop=True)                 # a quarter inside the TTM window the archive never had
    f = F.factors_for(newest_first(df), price=100.0, shares=10.0)
    assert np.isnan(f["margin"]) and np.isnan(f["rev_growth"])


def test_sue_is_the_surprise_over_the_history_of_surprises():
    eps = [1, 1, 1, 1, 1.1, 1.0, 1.2, 1.1, 1.05, 1.15, 3.0][:11]   # the newest quarter jumps
    q = newest_first(quarters(len(eps), eps=eps))
    f = F.factors_for(q, price=100.0, shares=10.0)
    assert f["sue"] > 3
    flat = newest_first(quarters(11, eps=[1.0] * 11))
    assert np.isnan(F.factors_for(flat, price=100.0, shares=10.0)["sue"])     # no history of surprises: undefined


def test_an_implausible_earnings_yield_is_dropped_as_a_split_artifact():
    q = newest_first(quarters(4, pat=1000.0))
    assert np.isnan(F.factors_for(q, price=1.0, shares=1.0)["ep"])


def test_known_quarters_uses_the_restatement_only_after_it_was_filed():
    df = quarters(4)
    rev = df.iloc[[3]].copy()
    rev["filed_at"] = pd.Timestamp("2020-06-01")
    rev["pat"] = 99.0
    both = pd.concat([df, rev], ignore_index=True)
    early = F.known_quarters(both, "2020-05-01")
    late = F.known_quarters(both, "2020-07-01")
    assert early.loc[early["period_end"] == rev["period_end"].iloc[0], "pat"].iloc[0] == 10.0
    assert late.loc[late["period_end"] == rev["period_end"].iloc[0], "pat"].iloc[0] == 99.0


def test_panel_only_sees_filings_public_on_the_session():
    fr = quarters(9, eps=list(range(1, 10)))
    shares = pd.DataFrame({"isin": ["I"], "filed_at": [pd.Timestamp("2019-01-01")], "shares": [10.0]})
    prices = pd.DataFrame({"isin": "I", "business_date": pd.to_datetime(["2020-01-15", "2021-06-30"]),
                           "close_raw": [100.0, 100.0]})
    p = F.build(fr, shares, prices, ["2020-01-15", "2021-06-30"])
    early = p[p["business_date"] == pd.Timestamp("2020-01-15")]
    late = p[p["business_date"] == pd.Timestamp("2021-06-30")]
    assert early.empty or early["margin"].isna().all()          # fewer than four filed quarters by then
    assert len(late) == 1 and np.isfinite(late["margin"].iloc[0])
