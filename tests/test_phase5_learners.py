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
    np.testing.assert_allclose(g.predict(X), X @ g.w)


def test_forest_factory_is_deterministic_and_shallow_enough_for_the_panel():
    model = LR.forest()
    assert model.n_estimators == 150
    assert model.min_samples_leaf == 300
    assert model.random_state == 0
    assert model.n_jobs == 4


def test_regime_switch_requires_prior_history_and_emits_choice_when_supported():
    dates = [date(2018, 1, 1) + timedelta(days=14 * i) for i in range(100)]
    parts = []
    for i, day in enumerate(dates):
        x = np.linspace(-1, 1, 100)
        if i % 2:
            fwd = -x
        else:
            fwd = x
        parts.append(pd.DataFrame({"d": day, "fwd20": fwd, "mom_12_1": x,
                                   "roc_20": x, "lineage": [f"L{j}" for j in range(100)]}))
    p = pd.concat(parts, ignore_index=True)
    market = pd.DataFrame({"vix": np.linspace(10, 30, len(dates)),
                           "trend": np.sin(np.arange(len(dates)))}, index=dates)
    # Too little prior monthly history means no classifier is fitted and no recommendation appears.
    assert LR.regime_switch(p, market, years=[2018], step=1).empty
    result = LR.regime_switch(p, market, years=[2020], step=1)
    assert len(result) > 0
    assert set(result["choice"]) <= {"momentum", "reversal"}
    assert np.isfinite(result["chosen"]).all()


def test_regime_switch_skips_small_cross_sections_and_missing_market_history():
    day = date(2018, 1, 1)
    small = pd.DataFrame({"d": [day] * 50, "fwd20": np.arange(50),
                          "mom_12_1": np.arange(50), "roc_20": np.arange(50)})
    market = pd.DataFrame({"vix": [15.0]}, index=[day])
    assert LR.regime_switch(small, market, years=[2020], step=1).empty

    full = pd.DataFrame({"d": [day] * 100, "fwd20": np.arange(100),
                         "mom_12_1": np.arange(100), "roc_20": np.arange(100)})
    future_market = pd.DataFrame({"vix": [15.0]}, index=[date(2020, 1, 1)])
    assert LR.regime_switch(full, future_market, years=[2020], step=1).empty


def test_the_lstm_walk_forward_predicts_only_its_test_years():
    pytest.importorskip("torch")                       # the `forecasting` extra, not `dev`
    p = panel(n_dates=180, n=60)
    out = LR.lstm_walk_forward(p, ["a", "b"], steps=5, years=range(2023, 2024), epochs=1, min_train=1000)
    assert len(out) and out["d"].min() >= date(2023, 1, 1)
