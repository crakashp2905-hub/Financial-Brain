from dataclasses import replace

import pytest

from financial_brain.evaluation.baseline_controls import (
    BaselineControl, BaselineControlError, BaselineKind, FrozenBaselineSet, StrategyEvaluationIdentity,
)


def _valid_set() -> FrozenBaselineSet:
    exp = StrategyEvaluationIdentity("exp-1", "a" * 64, "b" * 64, ("2020-01-01", "2025-12-31"),
                                     "c" * 64, "d" * 64, 0.4)
    controls = tuple(BaselineControl(kind, kind.value, "a" * 64, "b" * 64,
                                     ("2020-01-01", "2025-12-31"), "c" * 64,
                                     "d" * 64 if kind is BaselineKind.EXPOSURE_MATCHED else "e" * 64,
                                     0.4 if kind is BaselineKind.EQUAL_TURNOVER_RANDOM else 0.0,
                                     "f" * 64, "1" * 64) for kind in BaselineKind)
    return FrozenBaselineSet(exp, controls)


def test_all_six_baseline_controls_freeze_to_a_reproducible_identity() -> None:
    one = _valid_set()
    two = _valid_set()
    assert one.sha256() == two.sha256()
    assert len(one.sha256()) == 64


def test_missing_control_fails_closed() -> None:
    valid = _valid_set()
    with pytest.raises(BaselineControlError, match="exactly the six"):
        FrozenBaselineSet(valid.experiment, valid.controls[:-1])


def test_different_calendar_or_window_fails_closed() -> None:
    valid = _valid_set()
    broken = replace(valid.controls[0], calendar_sha256="9" * 64)
    with pytest.raises(BaselineControlError, match="different calendar"):
        FrozenBaselineSet(valid.experiment, (broken, *valid.controls[1:]))


def test_random_control_must_match_turnover() -> None:
    valid = _valid_set()
    controls = tuple(replace(c, turnover_fraction=0.1) if c.kind is BaselineKind.EQUAL_TURNOVER_RANDOM else c
                     for c in valid.controls)
    with pytest.raises(BaselineControlError, match="turnover"):
        FrozenBaselineSet(valid.experiment, controls)


def test_exposure_control_must_match_strategy_exposure() -> None:
    valid = _valid_set()
    controls = tuple(replace(c, exposure_profile_sha256="8" * 64)
                     if c.kind is BaselineKind.EXPOSURE_MATCHED else c for c in valid.controls)
    with pytest.raises(BaselineControlError, match="exposure"):
        FrozenBaselineSet(valid.experiment, controls)


def test_every_control_requires_source_evidence_hash() -> None:
    with pytest.raises(BaselineControlError, match="evidence"):
        BaselineControl(BaselineKind.CASH, "cash", "a" * 64, "b" * 64, ("2020", "2025"),
                        "c" * 64, "d" * 64, 0.0, "f" * 64, "bad")
