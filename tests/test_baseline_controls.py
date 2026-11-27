from dataclasses import replace

import pytest

from financial_brain.evaluation.baseline_controls import (
    BaselineControl, BaselineControlError, BaselineKind, BaselineReturnSeries, FrozenBaselineSet,
    StrategyEvaluationIdentity,
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


def test_comparison_reports_all_six_frozen_net_controls_on_exact_periods() -> None:
    frozen = _valid_set()
    controls = {control.kind: BaselineReturnSeries(
        kind=control.kind,
        series_id=control.series_id,
        net_returns=(("2025-01", 0.01), ("2025-02", 0.02)),
        source_sha256=control.evidence_sha256,
        construction_sha256=control.construction_sha256,
    ) for control in frozen.controls}
    report = frozen.compare(strategy_net_returns=(("2025-01", 0.03), ("2025-02", 0.01)),
                             strategy_source_sha256="9" * 64, series=controls)
    assert set(report["controls"]) == {kind.value for kind in BaselineKind}
    assert report["controls"][BaselineKind.CASH.value]["mean_net_excess"] == pytest.approx(0.005)
    assert report["periods"] == ["2025-01", "2025-02"]
    assert "not an alpha certification" in report["interpretation"]


def test_comparison_rejects_period_mismatch_or_changed_source_identity() -> None:
    frozen = _valid_set()
    controls = {control.kind: BaselineReturnSeries(
        kind=control.kind,
        series_id=control.series_id,
        net_returns=(("2025-01", 0.01), ("2025-02", 0.02)),
        source_sha256=control.evidence_sha256,
        construction_sha256=control.construction_sha256,
    ) for control in frozen.controls}
    strategy = (("2025-01", 0.01), ("2025-02", 0.02))
    broken = dict(controls)
    broken[BaselineKind.CASH] = replace(broken[BaselineKind.CASH], net_returns=(("2025-01", 0.01),))
    with pytest.raises(BaselineControlError, match="periods do not exactly match"):
        frozen.compare(strategy_net_returns=strategy, strategy_source_sha256="9" * 64, series=broken)
    broken = dict(controls)
    broken[BaselineKind.CASH] = replace(broken[BaselineKind.CASH], source_sha256="8" * 64)
    with pytest.raises(BaselineControlError, match="source hash"):
        frozen.compare(strategy_net_returns=strategy, strategy_source_sha256="9" * 64, series=broken)


def test_return_series_rejects_nonfinite_loss_and_duplicate_periods() -> None:
    args = dict(kind=BaselineKind.CASH, series_id="cash", source_sha256="a" * 64,
                construction_sha256="b" * 64)
    with pytest.raises(BaselineControlError, match="above -100%"):
        BaselineReturnSeries(net_returns=(("2025-01", -1.0),), **args)
    with pytest.raises(BaselineControlError, match="unique and chronological"):
        BaselineReturnSeries(net_returns=(("2025-01", 0.01), ("2025-01", 0.02)), **args)
