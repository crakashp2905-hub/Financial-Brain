"""The learned model as a live challenger: trained once with an embargo, scored by rank exactly as in training."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import learned_model as LM


def panel(n_dates=120, n=200, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(n_dates):
        d = date(2020, 1, 1) + timedelta(days=7 * k)
        a = rng.normal(0, 1, n)
        rows.append(pd.DataFrame({"d": d, "lineage": [f"L{i}" for i in range(n)], "a": a, "b": rng.normal(0, 1, n),
                                  "fwd20": 0.01 * a + rng.normal(0, 0.05, n)}))
    return pd.concat(rows, ignore_index=True)


def test_train_saves_the_model_with_its_feature_list_and_embargo(tmp_path):
    p = panel()
    meta = LM.train_final(p, ["a", "b"], model_path=tmp_path / "m.txt", meta_path=tmp_path / "m.json")
    assert meta["features"] == ["a", "b"]
    assert meta["trained_through"] == str(p["d"].max() - timedelta(days=30))
    booster, m2 = LM.load(tmp_path / "m.txt", tmp_path / "m.json")
    day = p[p["d"] == p["d"].max()].reset_index(drop=True)
    s = LM.score_frame(day, booster, m2["features"])
    assert np.corrcoef(s, day["a"])[0, 1] > 0.5            # it learned the planted feature


def test_training_artifacts_and_prediction_lineage_are_reproducible_and_frozen(tmp_path):
    p = panel(n_dates=35, n=210, seed=12)
    model_a, meta_a = tmp_path / "a.txt", tmp_path / "a.json"
    model_b, meta_b = tmp_path / "b.txt", tmp_path / "b.json"
    first = LM.train_final(p, ["a", "b"], model_path=model_a, meta_path=meta_a, version="frozen-v7")
    second = LM.train_final(p.sample(frac=1, random_state=2), ["a", "b"],
                            model_path=model_b, meta_path=meta_b, version="frozen-v7")
    assert first["version"] == "frozen-v7"
    assert first["training_data_sha256"] == second["training_data_sha256"]
    assert first["feature_schema_sha256"] == second["feature_schema_sha256"]
    assert first["model_sha256"] == second["model_sha256"]
    booster, metadata = LM.load(model_a, meta_a)
    scored = p[p["d"] == p["d"].max()].reset_index(drop=True)
    predictions = LM.score_frame(scored, booster, metadata["features"])
    lineage = LM.prediction_lineage(scored, metadata, predictions)
    assert len(lineage["lineage_sha256"]) == 64
    with pytest.raises(LM.LearnedModelError, match="immutable"):
        LM.train_final(p, ["a", "b"], model_path=model_a, meta_path=meta_a, version="frozen-v7")


def test_model_load_refuses_artifact_changed_after_freeze(tmp_path):
    p = panel(n_dates=35, n=210, seed=18)
    model, metadata = tmp_path / "model.txt", tmp_path / "model.json"
    LM.train_final(p, ["a", "b"], model_path=model, meta_path=metadata)
    model.write_text("tampered", encoding="utf-8")
    with pytest.raises(LM.LearnedModelError, match="artifact hash"):
        LM.load(model, metadata)


def test_the_challenger_joins_the_competition_only_when_scored():
    from financial_brain.evaluation import champion as C
    rng = np.random.default_rng(1)
    cs = pd.DataFrame({"lineage": [f"L{i}" for i in range(200)], "ticker": [f"T{i}" for i in range(200)],
                       "price": 100.0, "adv20": 1e8, "vol_60": rng.uniform(0.2, 0.5, 200), "mom_12_1": rng.normal(0, 0.3, 200),
                       "dist_52w_high": -rng.uniform(0, 0.4, 200), "ret_20d": rng.normal(0, 0.08, 200),
                       "turnover": 1e8, "ret_1d": 0.0, "ret_5d": 0.0, "rsi_14": 50.0, "adx_14": 20.0,
                       "atr_pctile_250": 0.5, "atr_14_pct": 0.02})
    assert "learned_model_top" not in C.picks(cs, as_of=date(2026, 10, 5))
    learned = pd.DataFrame({"lineage": cs["lineage"], "pred": np.arange(200)[::-1]})
    got = C.picks(cs, as_of=date(2026, 10, 5), learned=learned)["learned_model_top"]
    assert set(got["lineage"]) == {f"L{i}" for i in range(int(C.TOP_FRACTION * 200))}
