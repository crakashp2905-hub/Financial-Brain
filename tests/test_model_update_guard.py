from __future__ import annotations

from datetime import datetime, timezone

import pytest

from financial_brain.config import Config
from financial_brain.evaluation import model_update_guard as guard, timing
from financial_brain.storage.db import Database


@pytest.fixture
def con(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as connection:
        yield connection


def _model(weight: float) -> timing.Model:
    return timing.Model(
        weights=[weight] * len(timing.FEATURES),
        bias=0.1,
        threshold=0.6,
        fitted_on=("2015-01-01", "2022-12-31"),
        verified_on=("2024-01-01", "2026-01-01"),
    )


def _digest(model: timing.Model) -> str:
    return guard.model_identity_sha256(
        version=timing.VERSION,
        features=model.features,
        weights=model.weights,
        bias=model.bias,
        threshold=model.threshold,
        fitted_on=model.fitted_on,
        verified_on=model.verified_on,
    )


def test_locked_cohort_rejects_weight_update_and_drift_alarm_does_not_unlock(con):
    frozen = _model(0.1)
    timing.save(con, frozen)
    frozen_sha = _digest(frozen)
    guard.freeze_model_for_cohort(
        con,
        cohort_id="forward-2026-11",
        model_sha256=frozen_sha,
        protocol_sha256="a" * 64,
        locked_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
    )

    with pytest.raises(guard.ModelUpdateGuardError, match="blocked"):
        timing.save(con, _model(0.9))

    guard.record_drift_alarm(
        con,
        cohort_id="forward-2026-11",
        model_sha256=frozen_sha,
        drift_metric=2.4,
        alarm_id="alarm-1",
        at_utc=datetime(2026, 11, 3, tzinfo=timezone.utc),
    )
    with pytest.raises(guard.ModelUpdateGuardError, match="blocked"):
        timing.save(con, _model(0.9))
    assert guard.active_cohorts(con) == {"forward-2026-11": frozen_sha}
    assert timing.latest(con).weights == frozen.weights


def test_identical_model_save_is_a_noop_until_cohort_closes(con):
    frozen = _model(0.1)
    timing.save(con, frozen)
    frozen_sha = _digest(frozen)
    guard.freeze_model_for_cohort(
        con,
        cohort_id="forward-2026-12",
        model_sha256=frozen_sha,
        protocol_sha256="b" * 64,
        locked_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
    )
    before = con.execute("SELECT COUNT(*) FROM timing_models").fetchone()[0]
    timing.save(con, frozen)
    assert con.execute("SELECT COUNT(*) FROM timing_models").fetchone()[0] == before

    guard.close_cohort(
        con,
        cohort_id="forward-2026-12",
        model_sha256=frozen_sha,
        closure_evidence_sha256="c" * 64,
        closed_at=datetime(2026, 12, 1, tzinfo=timezone.utc),
    )
    updated = _model(0.2)
    timing.save(con, updated)
    assert timing.latest(con).weights == updated.weights


def test_model_identity_rejects_non_finite_weights():
    with pytest.raises(guard.ModelUpdateGuardError, match="finite"):
        guard.model_identity_sha256(
            version="t1",
            features=("x",),
            weights=[float("nan")],
            bias=0.0,
            threshold=None,
            fitted_on=("", ""),
            verified_on=("", ""),
        )
