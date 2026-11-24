import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.learning.prequential import (
    PredictionSeal,
    PrequentialError,
    PrequentialJournal,
    ResolvedOutcome,
    ShadowUpdate,
)


T0 = datetime(2026, 11, 24, 9, 15, tzinfo=timezone.utc)


def _prediction():
    return PredictionSeal("prediction-1", "model-v1", "features-v3", T0, T0,
                         "a" * 64, "b" * 64)


def _outcome():
    return ResolvedOutcome("outcome-1", "prediction-1", T0 + timedelta(hours=1), -0.002,
                           "c" * 64, "d" * 64, "e" * 64, "f" * 64)


def _update(**overrides):
    values = dict(update_id="update-1", parent_model_version="model-v1", model_version="model-v2",
                  updated_at_utc=T0 + timedelta(hours=2), parent_checkpoint_sha256="1" * 64,
                  changed_parameters_sha256="2" * 64, settled_outcome_ids=("outcome-1",),
                  reason="shadow update after horizon settled")
    values.update(overrides)
    return ShadowUpdate(**values)


def test_prequential_order_prediction_then_realized_outcome_then_shadow_update(tmp_path):
    path = tmp_path / "prequential.sqlite3"
    journal = PrequentialJournal(path)
    journal.seal_prediction(_prediction())
    journal.observe(_outcome())
    journal.record_shadow_update(_update())
    assert journal.event_counts() == {"predictions": 1, "outcomes": 1, "updates": 1}
    journal.close()
    reopened = PrequentialJournal(path)
    assert reopened.event_counts() == {"predictions": 1, "outcomes": 1, "updates": 1}
    reopened.close()


def test_outcome_cannot_precede_prediction_or_use_future_information(tmp_path):
    journal = PrequentialJournal(tmp_path / "prequential.sqlite3")
    with pytest.raises(PrequentialError, match="sealed prediction"):
        journal.observe(_outcome())
    with pytest.raises(PrequentialError, match="after its decision"):
        PredictionSeal("future", "model-v1", "features-v3", T0, T0 + timedelta(seconds=1),
                       "a" * 64, "b" * 64)
    journal.close()


def test_outcome_must_follow_prediction_and_settle_only_once(tmp_path):
    journal = PrequentialJournal(tmp_path / "prequential.sqlite3")
    journal.seal_prediction(_prediction())
    with pytest.raises(PrequentialError, match="after its prediction"):
        journal.observe(ResolvedOutcome("early", "prediction-1", T0, 0.0,
                                        "c" * 64, "d" * 64, "e" * 64, "f" * 64))
    journal.observe(_outcome())
    with pytest.raises(PrequentialError, match="already settled"):
        journal.observe(_outcome())
    journal.close()


def test_update_requires_settled_outcomes_and_shadow_only_mode(tmp_path):
    journal = PrequentialJournal(tmp_path / "prequential.sqlite3")
    journal.seal_prediction(_prediction())
    with pytest.raises(PrequentialError, match="already settled"):
        journal.record_shadow_update(_update())
    journal.observe(_outcome())
    with pytest.raises(PrequentialError, match="SHADOW_ONLY"):
        journal.record_shadow_update(_update(mode="LIVE_CASH"))
    with pytest.raises(PrequentialError, match="cannot precede"):
        journal.record_shadow_update(_update(updated_at_utc=T0))
    journal.close()


def test_journal_rows_are_append_only(tmp_path):
    journal = PrequentialJournal(tmp_path / "prequential.sqlite3")
    journal.seal_prediction(_prediction())
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        journal._con.execute("DELETE FROM prequential_predictions WHERE prediction_id='prediction-1'")
    journal.close()


def test_duplicate_prediction_and_duplicate_model_update_are_rejected(tmp_path):
    journal = PrequentialJournal(tmp_path / "prequential.sqlite3")
    journal.seal_prediction(_prediction())
    with pytest.raises(PrequentialError, match="already sealed"):
        journal.seal_prediction(_prediction())
    journal.observe(_outcome())
    journal.record_shadow_update(_update())
    with pytest.raises(PrequentialError, match="already recorded"):
        journal.record_shadow_update(_update(update_id="update-2"))
    journal.close()
