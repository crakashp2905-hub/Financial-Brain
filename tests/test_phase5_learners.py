"""More learners: each finds a planted feature on training years only."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import deep_library as DL
from financial_brain.evaluation import learners as LR


def panel(n_dates=150, n=150, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(n_dates):
        d = date(2018, 1, 1) + timedelta(days=14 * k)
        a = rng.normal(0, 1, n)
        rows.append(pd.DataFrame({"d": d, "lineage": [f"L{i}" for i in range(n)], "adv20": 1e9, "a": a,
                                  "b": rng.normal(0, 1, n), "mom_12_1": a, "roc_20": rng.normal(0, 1, n),
                                  "fwd20": 0.02 * a + rng.normal(0, 0.05, n)}))
    return pd.concat(rows, ignore_index=True)


@pytest.mark.parametrize("factory", [LR.svm, LR.mlp])
def test_sklearn_learners_find_the_planted_feature_walk_forward(factory):
    out = DL.walk_forward(panel(), ["a", "b"], years=range(2021, 2023), model_factory=factory)
    ic = out.groupby("d").apply(lambda x: x["pred"].corr(x["fwd20"], method="spearman")).mean()
    assert ic > 0.1


def test_the_genetic_weights_favour_the_planted_feature():
    p = panel(n_dates=40)
    X = p[["a", "b"]].to_numpy()
    g = LR.Genetic(pop=10, gens=5).fit(X, p["fwd20"].to_numpy(), p["d"].to_numpy())
    assert abs(g.w[0]) > abs(g.w[1])


def test_the_lstm_walk_forward_predicts_only_its_test_years():
    pytest.importorskip("torch")                       # the `forecasting` extra, not `dev`
    p = panel(n_dates=180, n=60)
    out = LR.lstm_walk_forward(p, ["a", "b"], steps=5, years=range(2023, 2024), epochs=1, min_train=1000)
    assert len(out) and out["d"].min() >= date(2023, 1, 1)
