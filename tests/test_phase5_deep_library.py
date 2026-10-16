"""The library's test harness: FDR control, a planted feature found and noise rejected, walk-forward without leakage."""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from financial_brain.evaluation import deep_library as DL


def test_bh_controls_false_discoveries():
    rng = np.random.default_rng(0)
    p = np.concatenate([rng.uniform(0, 1, 950), rng.uniform(0, 1e-5, 50)])
    keep = DL.bh(p)
    assert keep[950:].all() and keep[:950].sum() <= 5


def panel(n_dates=160, n=300, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    d0 = date(2018, 1, 1)
    for k in range(n_dates):
        d = d0 + timedelta(days=7 * k)
        good = rng.normal(0, 1, n)
        noise = rng.normal(0, 1, n)
        y5 = 0.01 * good + rng.normal(0, 0.05, n)
        rows.append(pd.DataFrame({"d": d, "lineage": [f"L{i}" for i in range(n)], "adv20": rng.uniform(1e7, 1e10, n),
                                  "good": good, "noise": noise, "fwd5": y5, "fwd20": y5 + rng.normal(0, 0.05, n),
                                  "rt": 0.003}))
    return DL.universe(pd.concat(rows, ignore_index=True))


def test_a_planted_feature_counts_and_noise_does_not():
    s = DL.sweep(panel(n_dates=260), ["good", "noise"]).set_index(["feature", "h"])   # runs into the 2022+ held-out years
    assert s.loc[("good", 5), "counts"] and s.loc[("good", 5), "ic"] > 0
    assert not s.loc[("noise", 5), "counts"] and not s.loc[("noise", 20), "counts"]


def test_walk_forward_never_trains_on_its_test_year():
    seen = []

    class Spy:
        def fit(self, X, y):
            seen.append(len(y))
            return self

        def predict(self, X):
            return np.zeros(len(X))

    p = panel(n_dates=260)
    out = DL.walk_forward(p, ["good", "noise"], years=range(2020, 2023), model_factory=Spy)
    assert out["d"].min() >= date(2020, 1, 1) and len(seen) == 3 and seen == sorted(seen)
    first_test = p[(p["d"] >= date(2020, 1, 1))]  # noqa: F841
    assert seen[0] <= (p["d"] < date(2020, 1, 1) - timedelta(days=30)).sum()


def test_books_charge_costs_only_on_entries():
    p = panel(n_dates=40)
    p["score"] = p["good"]
    r = DL.books(p, {"good": "score"}, ns=(20,))
    assert (r["net"] <= r["gross"]).all() and r["turnover"].iloc[0] == 1.0
