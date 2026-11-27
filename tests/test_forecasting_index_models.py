import pytest

from financial_brain.forecasting.index_models import (
    IndexModelError,
    fit_index_classifiers,
    predict_index_probabilities,
)


def training_data():
    labels = ["UP", "DOWN", "FLAT"] * 10
    features = [[float(i % 3 - 1), float(i % 5)] for i in range(len(labels))]
    return features, labels


def test_fits_simple_regularized_and_boosted_classifiers_with_complete_probabilities():
    features, labels = training_data()
    models = fit_index_classifiers(features, labels, ["return", "range"])
    assert set(models) == {"regularized_logistic", "hist_gradient_boosting"}
    for model in models.values():
        predictions = predict_index_probabilities(model, features[:4])
        assert len(predictions) == 4
        assert all(set(row) == {"UP", "DOWN", "FLAT"} for row in predictions)
        assert all(sum(row.values()) == pytest.approx(1.0) for row in predictions)
    assert models["regularized_logistic"].hyperparameters["C"] == 1.0
    assert models["hist_gradient_boosting"].hyperparameters["max_leaf_nodes"] == 7


def test_classifier_fit_and_prediction_reject_unfrozen_or_invalid_inputs():
    features, labels = training_data()
    with pytest.raises(IndexModelError, match="at least 20"):
        fit_index_classifiers(features[:10], labels[:10], ["return", "range"])
    with pytest.raises(IndexModelError, match="must align"):
        fit_index_classifiers(features, labels[:-1], ["return", "range"])
    with pytest.raises(IndexModelError, match="must be finite"):
        fit_index_classifiers([[float("nan"), 0.0]] * 20, ["UP"] * 20,
                              ["return", "range"])
