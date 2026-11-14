"""The learned model as a live challenger: trained once with an embargo, scored by rank exactly as in training."""
from datetime import date, datetime, timedelta, timezone
import json

import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import learned_model as LM
from financial_brain.evaluation import offline_training as OT


def panel(n_dates=120, n=200, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(n_dates):
        d = date(2020, 1, 1) + timedelta(days=7 * k)
        a = rng.normal(0, 1, n)
        rows.append(pd.DataFrame({"d": d, "lineage": [f"L{i}" for i in range(n)], "a": a, "b": rng.normal(0, 1, n),
                                  "fwd20": 0.01 * a + rng.normal(0, 0.05, n)}))
    return pd.concat(rows, ignore_index=True)


def timestamped(panel_data):
    frame = panel_data.copy()
    decisions = pd.to_datetime(frame["d"]).dt.tz_localize("UTC") + pd.Timedelta(hours=10)
    frame["decision_at_utc"] = decisions
    frame["label_matured_at_utc"] = decisions + pd.Timedelta(days=20)
    frame["dataset_version"] = "library-outcomes-v1"
    frame["feature_schema_version"] = "features-v1"
    return frame


def test_matured_offline_job_saves_reproducible_model_manifest(tmp_path):
    p = panel()
    data = timestamped(p)
    manifest = OT.run_offline_training_job(
        data, ["a", "b"], dataset_version="library-outcomes-v1",
        feature_schema_version="features-v1",
        trained_as_of=datetime(2023, 1, 1, tzinfo=timezone.utc),
        model_path=tmp_path / "m.txt", meta_path=tmp_path / "m.json", seed=0,
    )
    meta = json.loads((tmp_path / "m.json").read_text())
    assert meta["features"] == ["a", "b"]
    assert meta["trained_through"] == str(p["d"].max() - timedelta(days=1))
    assert meta["offline_training_manifest_sha256"] == manifest["manifest_sha256"]
    assert manifest["dataset_sha256"] and manifest["fit_dataset_sha256"]
    assert manifest["dataset_version"] == "library-outcomes-v1"
    assert manifest["feature_schema_version"] == "features-v1"
    assert manifest["eligible_matured_rows"] == len(p)
    assert manifest["quarantined_rows"] == 0
    assert manifest["seed"] == 0 and len(manifest["code_sha256"]) == 64
    assert manifest["environment"]["threads"] == 1
    assert manifest["environment"]["deterministic"] is True
    assert all(len(meta[key]) == 64 for key in ("model_id", "feature_schema_sha256", "training_data_sha256",
                                                "feature_source_sha256", "model_sha256", "code_sha256"))
    booster, m2 = LM.load(tmp_path / "m.txt", tmp_path / "m.json")
    day = p[p["d"] == p["d"].max()].reset_index(drop=True)
    s = LM.score_frame(day, booster, m2["features"])
    assert np.corrcoef(s, day["a"])[0, 1] > 0.5            # it learned the planted feature


def test_offline_job_quarantines_unknown_and_not_yet_mature_outcomes(tmp_path):
    p = timestamped(panel(n_dates=30, n=50))
    as_of = datetime(2021, 1, 1, tzinfo=timezone.utc)
    pending = p.iloc[[0]].copy()
    pending.loc[:, "lineage"] = "PENDING"
    pending.loc[:, "label_matured_at_utc"] = pd.Timestamp(as_of) + pd.Timedelta(days=1)
    unknown = p.iloc[[1]].copy()
    unknown.loc[:, "lineage"] = "UNKNOWN"
    unknown.loc[:, "label_matured_at_utc"] = pd.NaT
    source = pd.concat([p, pending, unknown], ignore_index=True)
    manifest = OT.run_offline_training_job(
        source, ["a", "b"], dataset_version="library-outcomes-v1",
        feature_schema_version="features-v1", trained_as_of=as_of,
        model_path=tmp_path / "m.txt", meta_path=tmp_path / "m.json", seed=4,
    )
    assert manifest["input_rows"] == len(p) + 2
    assert manifest["eligible_matured_rows"] == len(p)
    assert manifest["quarantined_rows"] == 2


def test_offline_job_rejects_unversioned_or_naive_timestamp_data(tmp_path):
    source = timestamped(panel(n_dates=2, n=2))
    with pytest.raises(OT.OfflineTrainingError, match="timezone"):
        OT.run_offline_training_job(
            source.assign(decision_at_utc="2020-01-01T10:00:00"), ["a", "b"],
            dataset_version="library-outcomes-v1", feature_schema_version="features-v1",
            trained_as_of=datetime(2021, 1, 1, tzinfo=timezone.utc),
            model_path=tmp_path / "m.txt", meta_path=tmp_path / "m.json",
        )
    with pytest.raises(OT.OfflineTrainingError, match="dataset_version"):
        OT.run_offline_training_job(
            source, ["a", "b"], dataset_version="unregistered-version",
            feature_schema_version="features-v1",
            trained_as_of=datetime(2021, 1, 1, tzinfo=timezone.utc),
            model_path=tmp_path / "m.txt", meta_path=tmp_path / "m.json",
        )


def test_prediction_batch_digest_is_order_independent_and_content_sensitive():
    frame = pd.DataFrame({"lineage": ["L2", "L1"], "pred": [0.2, -0.1]})
    digest = LM.prediction_batch_sha256(frame, as_of=date(2026, 11, 2), model_digest="m" * 64)
    assert digest == LM.prediction_batch_sha256(frame.iloc[::-1], as_of=date(2026, 11, 2), model_digest="m" * 64)
    changed = frame.assign(pred=[0.3, -0.1])
    assert digest != LM.prediction_batch_sha256(changed, as_of=date(2026, 11, 2), model_digest="m" * 64)


def test_prediction_cache_requires_matching_model_and_batch_hash(tmp_path):
    model_path = tmp_path / "model.bin"
    model_path.write_bytes(b"model")
    meta_path = tmp_path / "meta.json"
    manifest_name = "m.training.json"
    code_sha256 = "c" * 64
    meta = {"model_id": "a" * 64, "feature_schema_sha256": "b" * 64,
            "model_sha256": LM._file_sha256(model_path),
            "offline_training_manifest_file": manifest_name,
            "offline_training_code_sha256": code_sha256, "features": ["a", "b"], "seed": 0}
    manifest = {
        "schema_version": "fb.offline-training-job.v1",
        "maturity_contract": "label_matured_at_utc is known and <= trained_as_of_utc",
        "dataset_version": "test-data-v1", "feature_schema_version": "test-schema-v1",
        "features": meta["features"], "seed": meta["seed"],
        "training_parameters": {"n_jobs": 1, "deterministic": True, "force_col_wise": True},
        "environment": {"python": "3.13", "numpy": "1", "pandas": "2",
                         "lightgbm": "4", "threads": 1, "deterministic": True},
        "dataset_sha256": "d" * 64, "fit_dataset_sha256": "e" * 64,
        "model_id": meta["model_id"], "model_sha256": meta["model_sha256"],
        "feature_schema_sha256": meta["feature_schema_sha256"], "code_sha256": code_sha256,
        "trained_as_of_utc": "2026-11-02T00:00:00+00:00",
        "max_eligible_label_matured_at_utc": "2026-11-01T00:00:00+00:00",
        "input_rows": 2, "eligible_matured_rows": 2, "quarantined_rows": 0,
    }
    manifest["manifest_sha256"] = OT._manifest_sha256(manifest)
    meta["offline_training_manifest_sha256"] = manifest["manifest_sha256"]
    (tmp_path / manifest_name).write_text(json.dumps(manifest))
    frame = pd.DataFrame({"lineage": ["L1", "L2"], "pred": [0.1, 0.2],
                          "model_id": [meta["model_id"]] * 2,
                          "feature_schema_sha256": [meta["feature_schema_sha256"]] * 2})
    batch_digest = LM.prediction_batch_sha256(frame, as_of=date(2026, 11, 2), model_digest=meta["model_id"])
    frame["prediction_batch_sha256"] = batch_digest
    assert LM._valid_prediction_cache(frame, as_of=date(2026, 11, 2), meta=meta,
                                      model_path=model_path, meta_path=meta_path)
    frame.loc[0, "pred"] = 0.9
    assert not LM._valid_prediction_cache(frame, as_of=date(2026, 11, 2), meta=meta,
                                          model_path=model_path, meta_path=meta_path)
    frame.loc[0, "pred"] = 0.1
    (tmp_path / manifest_name).write_text('{"manifest_sha256":"tampered"}')
    assert not LM._valid_prediction_cache(frame, as_of=date(2026, 11, 2), meta=meta,
                                          model_path=model_path, meta_path=meta_path)
    (tmp_path / manifest_name).write_text(json.dumps(manifest))
    model_path.write_bytes(b"changed")
    assert not LM._valid_prediction_cache(frame, as_of=date(2026, 11, 2), meta=meta,
                                          model_path=model_path, meta_path=meta_path)


def test_load_rejects_model_artifact_changed_after_manifest(tmp_path):
    panel_data = panel(n_dates=30, n=400)
    model_path, meta_path = tmp_path / "m.txt", tmp_path / "m.json"
    LM.train_final(panel_data, ["a", "b"], model_path=model_path, meta_path=meta_path)
    with pytest.raises(LM.LearnedModelError, match="matured offline-training manifest"):
        LM.load(model_path, meta_path)
    with model_path.open("ab") as artifact:
        artifact.write(b"tamper")
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
