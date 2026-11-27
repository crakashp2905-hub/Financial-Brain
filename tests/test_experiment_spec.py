"""Frozen strategy-independent experiment registration contract."""
from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from financial_brain.research.experiment_spec import ExperimentSpec, ExperimentSpecError


def spec(**overrides):
    values = dict(experiment_id="exp-1", hypothesis="Intraday continuation after opening range",
                  economic_mechanism="Delayed reaction to concentrated opening order flow",
                  universe_definition="NSE cash EQ point-in-time eligible instruments",
                  decision_clock="NSE exchange event time; available-at cutoff",
                  parameter_bounds=(("range_minutes", 5, 30), ("risk_fraction", 0.001, 0.01)),
                  evaluation_start=date(2024, 1, 1), evaluation_end=date(2025, 1, 1),
                  benchmark="Matched equal-turnover cash benchmark",
                  failure_criteria=("after-cost net alpha <= 0", "max drawdown exceeds 10%"))
    values.update(overrides)
    return ExperimentSpec(**values)


def test_experiment_spec_freezes_all_required_fields_and_hashes_deterministically():
    registered = spec()
    assert registered.body()["schema"] == "fb.experiment_spec.v1"
    assert len(registered.sha256()) == 64
    assert registered.sha256() == spec().sha256()
    with pytest.raises(FrozenInstanceError):
        registered.experiment_id = "changed"


@pytest.mark.parametrize("change", [
    {"economic_mechanism": ""},
    {"decision_clock": ""},
    {"parameter_bounds": ()},
    {"evaluation_end": date(2023, 1, 1)},
    {"failure_criteria": ()},
    {"parameter_bounds": (("x", 2, 1),)},
])
def test_incomplete_or_invalid_specs_are_rejected(change):
    with pytest.raises(ExperimentSpecError):
        spec(**change)
