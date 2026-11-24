import numpy as np
import pytest

from financial_brain.portfolio.constraints import WeightProjectionError, project_feasible_weights


def test_long_only_projection_clips_desk_limits_and_preserves_cash_budget() -> None:
    result = project_feasible_weights(
        [0.8, 0.4, -0.1], lower=[0.0, 0.0, 0.0], upper=[0.5, 0.3, 0.2],
        gross_budget=0.6, allow_short=False,
    )
    assert np.allclose(result["weights"], [0.375, 0.225, 0.0])
    assert result["gross_exposure"] == pytest.approx(0.6)
    assert result["net_exposure"] == pytest.approx(0.6)
    assert result["short_exposure"] == 0.0
    assert result["feasible"] is True


def test_short_enabled_projection_reports_signed_gross_and_net() -> None:
    result = project_feasible_weights(
        [0.8, -0.6], lower=[-0.4, -0.3], upper=[0.7, 0.2],
        gross_budget=0.75, allow_short=True, max_net_exposure=0.25,
    )
    weights = result["weights"]
    assert np.allclose(weights, [0.4375, -0.1875])
    assert result["long_exposure"] == pytest.approx(0.4375)
    assert result["short_exposure"] == pytest.approx(-0.1875)
    assert result["gross_exposure"] == pytest.approx(0.625)
    assert result["net_exposure"] == pytest.approx(0.25)


@pytest.mark.parametrize("kwargs", [
    {"lower": [0.1], "upper": [1.0], "allow_short": False},
    {"lower": [-1.0], "upper": [1.0], "allow_short": False},
    {"lower": [0.0, 0.0], "upper": [1.0], "allow_short": False},
    {"lower": [0.0], "upper": [1.0], "allow_short": True, "gross_budget": -1.0},
])
def test_invalid_or_non_cash_feasible_constraints_are_rejected(kwargs: dict) -> None:
    args = {"lower": [0.0], "upper": [1.0], "allow_short": False, "gross_budget": 1.0}
    args.update(kwargs)
    with pytest.raises(WeightProjectionError):
        project_feasible_weights([0.5], **args)


def test_zero_budget_projects_every_sleeve_to_cash() -> None:
    result = project_feasible_weights(
        [0.4, -0.2], lower=[-0.5, -0.5], upper=[0.5, 0.5],
        gross_budget=0.0, allow_short=True,
    )
    assert np.array_equal(result["weights"], np.zeros(2))
    assert result["gross_exposure"] == 0.0
    assert result["net_exposure"] == 0.0
