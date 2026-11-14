"""The learned model as a live challenger: trained once with an embargo, scored by rank exactly as in training."""
from datetime import date, timedelta

import numpy as np
import pandas as pd

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
    assert meta["embargo_semantics"] == "calendar-day proxy; label-end timestamps unavailable"
    assert all(len(meta[key]) == 64 for key in ("model_id", "feature_schema_sha256", "training_data_sha256",
                                                "feature_source_sha256", "model_sha256", "code_sha256"))
    booster, m2 = LM.load(tmp_path / "m.txt", tmp_path / "m.json")
    day = p[p["d"] == p["d"].max()].reset_index(drop=True)
    s = LM.score_frame(day, booster, m2["features"])
    assert np.corrcoef(s, day["a"])[0, 1] > 0.5            # it learned the planted feature


def test_prediction_batch_digest_is_order_independent_and_content_sensitive():
    frame = pd.DataFrame({"lineage": ["L2", "L1"], "pred": [0.2, -0.1]})
    digest = LM.prediction_batch_sha256(frame, as_of=date(2026, 11, 2), model_digest="m" * 64)
    assert digest == LM.prediction_batch_sha256(frame.iloc[::-1], as_of=date(2026, 11, 2), model_digest="m" * 64)
    changed = frame.assign(pred=[0.3, -0.1])
    assert digest != LM.prediction_batch_sha256(changed, as_of=date(2026, 11, 2), model_digest="m" * 64)


def test_prediction_cache_requires_matching_model_and_batch_hash(tmp_path):
    model_path = tmp_path / "model.bin"
    model_path.write_bytes(b"model")
    meta = {"model_id": "a" * 64, "feature_schema_sha256": "b" * 64,
            "model_sha256": LM._file_sha256(model_path)}
    frame = pd.DataFrame({"lineage": ["L1", "L2"], "pred": [0.1, 0.2],
                          "model_id": [meta["model_id"]] * 2,
                          "feature_schema_sha256": [meta["feature_schema_sha256"]] * 2})
    batch_digest = LM.prediction_batch_sha256(frame, as_of=date(2026, 11, 2), model_digest=meta["model_id"])
    frame["prediction_batch_sha256"] = batch_digest
    assert LM._valid_prediction_cache(frame, as_of=date(2026, 11, 2), meta=meta, model_path=model_path)
    frame.loc[0, "pred"] = 0.9
    assert not LM._valid_prediction_cache(frame, as_of=date(2026, 11, 2), meta=meta, model_path=model_path)
    frame.loc[0, "pred"] = 0.1
    model_path.write_bytes(b"changed")
    assert not LM._valid_prediction_cache(frame, as_of=date(2026, 11, 2), meta=meta, model_path=model_path)


def test_load_rejects_model_artifact_changed_after_manifest(tmp_path):
    panel_data = panel(n_dates=30, n=400)
    model_path, meta_path = tmp_path / "m.txt", tmp_path / "m.json"
    LM.train_final(panel_data, ["a", "b"], model_path=model_path, meta_path=meta_path)
    with model_path.open("ab") as artifact:
        artifact.write(b"tamper")
    import pytest
    with pytest.raises(LM.LearnedModelError, match="digest differs"):
        LM.load(model_path, meta_path)


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
