from __future__ import annotations

import pytest

from financial_brain.kernel.learning_tools import (
    brier_score,
    calibration_bins,
    certificate,
    challenger_wins,
    fit_simple_linear,
    majority_classifier,
    mse,
    optional_dependency_present,
)


def test_linear_fit_recovers_a_known_line() -> None:
    slope, intercept = fit_simple_linear([0.0, 1.0, 2.0, 3.0], [1.0, 3.0, 5.0, 7.0])
    assert slope == pytest.approx(2.0)
    assert intercept == pytest.approx(1.0)


def test_challenger_must_strictly_beat_incumbent() -> None:
    assert challenger_wins(0.20, 0.15) is True
    assert challenger_wins(0.20, 0.20) is False


def test_majority_baseline_predicts_the_training_majority() -> None:
    assert majority_classifier([1, 1, 0, 1, 0]) == 1
    with pytest.raises(ValueError):
        majority_classifier([])


def test_brier_score_and_calibration_bins() -> None:
    assert brier_score([1.0, 0.0], [1, 0]) == pytest.approx(0.0)
    assert brier_score([0.5, 0.5], [1, 0]) == pytest.approx(0.25)
    bins = calibration_bins([0.1, 0.15, 0.9], [0, 0, 1], bins=2)
    assert bins[0][2] == 2 and bins[1][2] == 1
    assert bins[0][1] == pytest.approx(0.0)


def test_mse_requires_aligned_data() -> None:
    assert mse([1.0, 2.0], [1.0, 4.0]) == pytest.approx(2.0)
    with pytest.raises(ValueError):
        mse([1.0], [1.0, 2.0])


def test_optional_dependency_check_does_not_import_it() -> None:
    assert optional_dependency_present("definitely_not_a_real_module_xyz") is False
    assert optional_dependency_present("json") is True


def test_certificate_changes_when_any_input_or_version_changes() -> None:
    a = certificate({"data": "h1"}, {"python": "3.12"})
    assert a == certificate({"data": "h1"}, {"python": "3.12"})
    assert a != certificate({"data": "h2"}, {"python": "3.12"})
    assert a != certificate({"data": "h1"}, {"python": "3.13"})
