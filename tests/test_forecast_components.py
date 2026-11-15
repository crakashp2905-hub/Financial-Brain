from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.evaluation.forecast_components import (
    ALPHA_TARGET,
    FILL_TARGET,
    RISK_TARGET,
    AlphaForecast,
    FillProbabilityForecast,
    ForecastBundle,
    ForecastComponentError,
    RiskForecast,
    calibrate_alpha_component,
    calibrate_fill_component,
    calibrate_risk_component,
    evaluate_alpha_quality,
    evaluate_fill_quality,
    evaluate_risk_quality,
    write_alpha_validation_artifacts,
)

AS_OF = datetime(2026, 2, 1, tzinfo=timezone.utc)
MODELS = {"alpha": "a" * 64, "fill": "b" * 64, "risk": "c" * 64}


def _rows(component, values):
    base = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    output = []
    for index, fields in enumerate(values):
        target = {"alpha": ALPHA_TARGET, "fill": FILL_TARGET, "risk": RISK_TARGET}[component]
        output.append({
            "split": "VALIDATION", "model_sha256": MODELS[component], "target_schema": target,
            "decision_sha256": f"{index + 1:064x}", "decision_at_utc": base,
            "target_matured_at_utc": base + timedelta(days=1),
            "target_revised_at_utc": base + timedelta(days=1), **fields,
        })
    return output


def test_forecast_bundle_keeps_alpha_fill_and_risk_targets_separate():
    bundle = ForecastBundle(
        AlphaForecast(MODELS["alpha"], 35.0, 5),
        FillProbabilityForecast(MODELS["fill"], 0.7, 30),
        RiskForecast(MODELS["risk"], 120.0, 0.95, 5),
    )
    result = bundle.as_dict()
    assert set(result) == {"alpha", "execution_fill", "risk"}
    assert result["alpha"]["target_schema"] == ALPHA_TARGET
    assert result["execution_fill"]["target_schema"] == FILL_TARGET
    assert result["risk"]["target_schema"] == RISK_TARGET
    assert result["alpha"]["model_sha256"] != result["execution_fill"]["model_sha256"]


def test_alpha_quality_is_regression_against_realized_net_alpha_only():
    rows = _rows("alpha", [
        {"expected_net_alpha_bps": 1.0, "realized_net_alpha_bps": 1.0},
        {"expected_net_alpha_bps": 2.0, "realized_net_alpha_bps": 2.0},
        {"expected_net_alpha_bps": 3.0, "realized_net_alpha_bps": 2.0},
        {"expected_net_alpha_bps": 4.0, "realized_net_alpha_bps": 4.0},
    ])
    report = evaluate_alpha_quality(rows, as_of=AS_OF, model_sha256=MODELS["alpha"])
    assert report["component"] == "ALPHA"
    assert report["sample_count"] == 4
    assert report["mae_bps"] == 0.25
    assert report["calibration_slope"] is not None


def test_fill_quality_uses_binary_fill_by_deadline_target_and_probability_metrics():
    rows = _rows("fill", [
        {"fill_probability": 0.1, "filled_by_deadline": False},
        {"fill_probability": 0.3, "filled_by_deadline": 0},
        {"fill_probability": 0.7, "filled_by_deadline": 1},
        {"fill_probability": 0.9, "filled_by_deadline": True},
    ])
    report = evaluate_fill_quality(rows, as_of=AS_OF, model_sha256=MODELS["fill"])
    assert report["component"] == "EXECUTION_FILL"
    assert report["brier_score"] == pytest.approx(0.05)
    assert report["expected_calibration_error"] == pytest.approx(0.2)
    assert len(report["reliability_bins"]) == 4


def test_risk_quality_uses_downside_quantile_coverage_and_pinball_loss():
    rows = _rows("risk", [
        {"loss_quantile_bps": 100, "confidence_level": 0.75, "realized_adverse_excursion_bps": 50},
        {"loss_quantile_bps": 200, "confidence_level": 0.75, "realized_adverse_excursion_bps": 250},
        {"loss_quantile_bps": 300, "confidence_level": 0.75, "realized_adverse_excursion_bps": 300},
        {"loss_quantile_bps": 400, "confidence_level": 0.75, "realized_adverse_excursion_bps": 500},
    ])
    report = evaluate_risk_quality(rows, as_of=AS_OF, model_sha256=MODELS["risk"])
    assert report["component"] == "RISK"
    assert report["empirical_coverage"] == 0.5
    assert report["coverage_error"] == -0.25
    assert report["mean_pinball_loss_bps"] == pytest.approx(31.25)


@pytest.mark.parametrize("component,evaluator,fields", [
    ("alpha", evaluate_alpha_quality, {"expected_net_alpha_bps": 1, "realized_net_alpha_bps": 1}),
    ("fill", evaluate_fill_quality, {"fill_probability": 0.5, "filled_by_deadline": 1}),
    ("risk", evaluate_risk_quality,
     {"loss_quantile_bps": 2, "confidence_level": 0.9, "realized_adverse_excursion_bps": 1}),
])
def test_component_metrics_require_untouched_validation_and_mature_targets(component, evaluator, fields):
    rows = _rows(component, [fields, fields])
    with pytest.raises(ForecastComponentError, match="VALIDATION"):
        evaluator([{**row, "split": "DEVELOPMENT"} for row in rows],
                  as_of=AS_OF, model_sha256=MODELS[component])
    rows[0]["target_revised_at_utc"] = AS_OF + timedelta(seconds=1)
    with pytest.raises(ForecastComponentError, match="not known"):
        evaluator(rows, as_of=AS_OF, model_sha256=MODELS[component])


def test_independent_calibrator_rejects_mixed_model_artifacts():
    rows = _rows("fill", [
        {"fill_probability": 0.2, "filled_by_deadline": 0},
        {"fill_probability": 0.8, "filled_by_deadline": 1},
    ])
    rows[1]["model_sha256"] = MODELS["alpha"]
    with pytest.raises(ForecastComponentError, match="mixes component model"):
        evaluate_fill_quality(rows, as_of=AS_OF, model_sha256=MODELS["fill"])


def test_fill_metrics_cannot_be_called_on_alpha_target_rows():
    rows = _rows("alpha", [
        {"expected_net_alpha_bps": 1, "realized_net_alpha_bps": 1},
        {"expected_net_alpha_bps": 2, "realized_net_alpha_bps": 2},
    ])
    with pytest.raises(ForecastComponentError, match="target schemas"):
        evaluate_fill_quality(rows, as_of=AS_OF, model_sha256=MODELS["alpha"])


def test_three_development_calibrators_have_separate_targets_and_hashes():
    validation_start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    frozen_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    alpha, fill, risk = [], [], []
    base = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    for index in range(40):
        times = {"split": "DEVELOPMENT", "decision_sha256": f"{index + 1:064x}",
                 "decision_at_utc": base + timedelta(days=index),
                 "target_matured_at_utc": base + timedelta(days=index + 1),
                 "target_revised_at_utc": base + timedelta(days=index + 1)}
        x = float(index + 1)
        alpha.append({**times, "model_sha256": MODELS["alpha"], "target_schema": ALPHA_TARGET,
                      "expected_net_alpha_bps": x, "realized_net_alpha_bps": 2 * x + 5})
        p = x / 41
        fill.append({**times, "model_sha256": MODELS["fill"], "target_schema": FILL_TARGET,
                     "fill_probability": p, "filled_by_deadline": int(p >= 0.5)})
        risk.append({**times, "model_sha256": MODELS["risk"], "target_schema": RISK_TARGET,
                     "loss_quantile_bps": 100 + x, "realized_adverse_excursion_bps": 1.2 * (100 + x)})
    alpha_fit = calibrate_alpha_component(alpha, as_of=frozen_at, validation_start=validation_start,
                                          model_sha256=MODELS["alpha"])
    fill_fit = calibrate_fill_component(fill, as_of=frozen_at, validation_start=validation_start,
                                        model_sha256=MODELS["fill"])
    risk_fit = calibrate_risk_component(risk, as_of=frozen_at, validation_start=validation_start,
                                        model_sha256=MODELS["risk"], confidence_level=0.9)
    assert alpha_fit.apply(10) == pytest.approx(25)
    assert alpha_fit.sha256 != fill_fit.sha256 != risk_fit.sha256
    assert len({alpha_fit.development_data_sha256, fill_fit.development_data_sha256,
                risk_fit.development_data_sha256}) == 3
    assert 0 <= fill_fit.apply(0.25) <= fill_fit.apply(0.75) <= 1
    assert risk_fit.apply(100) == pytest.approx(120)


def test_calibration_fails_closed_on_non_development_data():
    row = {"split": "VALIDATION", "model_sha256": MODELS["alpha"], "target_schema": ALPHA_TARGET,
           "decision_sha256": "1" * 64, "decision_at_utc": AS_OF - timedelta(days=4),
           "target_matured_at_utc": AS_OF - timedelta(days=3),
           "target_revised_at_utc": AS_OF - timedelta(days=3),
           "expected_net_alpha_bps": 1, "realized_net_alpha_bps": 2}
    with pytest.raises(ForecastComponentError, match="DEVELOPMENT"):
        calibrate_alpha_component([row] * 30, as_of=AS_OF, validation_start=AS_OF + timedelta(days=1),
                                  model_sha256=MODELS["alpha"])


def test_untouched_validation_writes_return_uncertainty_and_trading_impact_curves(tmp_path):
    rows = _rows("alpha", [
        {"expected_net_alpha_bps": float(index),
         "realized_net_alpha_bps": float(index) * 0.8,
         "uncertainty_standard_error_bps": float(index) / 2}
        for index in range(1, 21)
    ])
    report = write_alpha_validation_artifacts(rows, as_of=AS_OF, model_sha256=MODELS["alpha"],
                                              output_dir=tmp_path, bins=5)
    assert report["split"] == "VALIDATION"
    assert len(report["alpha_calibration_curve"]) == 5
    assert len(report["uncertainty_calibration_curve"]) == 5
    assert len(report["net_alpha_trading_impact_curve"]) == 5
    assert len(report["validation_data_sha256"]) == len(report["svg_sha256"]) == 64
    svg = (tmp_path / "alpha-validation-calibration.svg").read_text()
    assert "Net alpha calibration" in svg
    assert "Uncertainty interval coverage" in svg
    assert "Net alpha selection impact" in svg
    assert report["svg_sha256"]
    assert report["json_sha256"]
