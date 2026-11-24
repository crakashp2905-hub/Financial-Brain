from datetime import date, timedelta

import numpy as np
import pytest

from financial_brain.forward_edge.pattern_uplift import (
    PatternObservation,
    PatternUpliftError,
    estimate_pattern_uplift,
)


def _sample(pattern_effect: float) -> tuple[PatternObservation, ...]:
    rng = np.random.default_rng(721)
    rows = []
    for day_index in range(12):
        day = date(2026, 1, 1) + timedelta(days=day_index)
        day_effect = (day_index - 5.5) * 0.0007
        for name_index in range(20):
            momentum = float(rng.normal())
            high_distance = float(rng.normal())
            volatility = float(rng.uniform(0.1, 0.8))
            signal = bool(momentum + 0.35 * high_distance + rng.normal(0, 0.7) > 0.3)
            net = (0.012 * momentum - 0.008 * high_distance - 0.004 * volatility
                   + day_effect + pattern_effect * signal)
            rows.append(PatternObservation(day, f"I{name_index}", signal, momentum,
                                           high_distance, volatility, net))
    return tuple(rows)


def test_factor_adjusted_pattern_coefficient_removes_known_factor_only_effect() -> None:
    report = estimate_pattern_uplift(_sample(0.0))
    assert report.observations == 240
    assert report.pattern_observations + report.control_observations == 240
    assert report.factor_adjusted_pattern_coefficient == pytest.approx(0.0, abs=1e-12)
    assert report.inference_status.startswith("DESCRIPTIVE_ONLY")


def test_adjusted_coefficient_detects_injected_incremental_pattern_component() -> None:
    report = estimate_pattern_uplift(_sample(0.015))
    assert report.factor_adjusted_pattern_coefficient == pytest.approx(0.015, abs=1e-12)


def test_pattern_uplift_requires_both_groups_and_identifiable_design() -> None:
    rows = tuple(PatternObservation(date(2026, 1, i // 2 + 1), f"I{i}", True,
                                    float(i), float(i % 3), float(i % 4), 0.01)
                 for i in range(20))
    with pytest.raises(PatternUpliftError, match="both pattern"):
        estimate_pattern_uplift(rows)
    with pytest.raises(PatternUpliftError, match="at least 12"):
        estimate_pattern_uplift(rows[:5])
