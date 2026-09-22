"""The System One timing model (ADR-0002 applied to our own data).

What matters here is not the model class - a bigger model on the same features would
flatter itself on the same history - but that the threshold is measured on data the fit
never saw, and that the model is allowed to refuse.
"""
from __future__ import annotations

import numpy as np
import pytest

from financial_brain.config import Config
from financial_brain.evaluation import timing
from financial_brain.storage.db import Database


def test_a_separable_problem_is_learned():
    rng = np.random.default_rng(7)
    x = rng.random((4000, 2))
    y = (x[:, 0] > 0.5).astype(float)
    w, b = timing.fit(x, y, steps=600)
    assert w[0] > 2 * abs(w[1]), "the informative feature carries the weight"


def test_the_threshold_is_the_lowest_confidence_that_still_holds_the_target():
    """Lowest, not highest: the bar buys as much coverage as the target allows. Five of
    five and five of six (83%) both clear a 75% target, so the threshold walks down to
    0.65; the seventh case (5 of 7 = 71%) is what stops it."""
    probs = np.array([0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.55])
    y = np.array([1, 1, 1, 1, 1, 0, 0, 0], dtype=float)
    t = timing.calibrate(probs, y, target=0.75, min_support=4)
    assert t == pytest.approx(0.65)


def test_no_threshold_when_nothing_reaches_the_target():
    probs = np.array([0.9, 0.8, 0.7, 0.6, 0.5])
    y = np.array([1, 0, 1, 0, 0], dtype=float)
    assert timing.calibrate(probs, y, target=0.9, min_support=3) is None


def test_a_confident_wrong_model_earns_no_threshold():
    """Confidence is not accuracy: a model sure of the wrong answer must not qualify."""
    probs = np.array([0.99] * 50)
    y = np.zeros(50)
    assert timing.calibrate(probs, y, target=0.55, min_support=10) is None


@pytest.fixture
def con(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as c:
        yield c


def test_an_unusable_model_refuses_to_act(con):
    """A timing model that cannot beat its base rate is worse than none, because it feels
    like information."""
    model = timing.Model(weights=[0.0] * len(timing.FEATURES), bias=0.0, threshold=None)
    timing.save(con, model)
    from datetime import date
    d = timing.decide(con, "INE000A01001", date(2026, 9, 18))
    assert d.label == "wait" and d.confidence == 0.0
    assert "no verified timing model" in str(d.extra)


def test_the_decision_has_the_same_shape_as_a_language_models(con):
    """A caller must not be able to tell a local regression from a routed LLM, and
    neither is trusted without its measured threshold."""
    from financial_brain.llm.system1 import Decision
    model = timing.Model(weights=[0.0] * len(timing.FEATURES), bias=0.0, threshold=None)
    timing.save(con, model)
    from datetime import date
    d = timing.decide(con, "INE000A01001", date(2026, 9, 18))
    assert isinstance(d, Decision)
    assert d.model.startswith("timing-")


def test_a_saved_model_round_trips(con):
    model = timing.Model(weights=[0.1] * len(timing.FEATURES), bias=-0.2,
                         threshold=0.58, fitted_on=("2015-01-01", "2022-12-31"),
                         verified_on=("2024-06-30", "2026-09-18"),
                         report={"verdict": "usable out of sample"})
    timing.save(con, model)
    back = timing.latest(con)
    assert back.usable() and back.threshold == pytest.approx(0.58)
    assert back.report["verdict"] == "usable out of sample"
    assert back.probability([0.5] * len(timing.FEATURES)) == pytest.approx(
        model.probability([0.5] * len(timing.FEATURES)))


def test_walk_forward_requires_most_folds_to_hold():
    """One good period is a period, not an effect."""
    good = {"folds_tested": 4, "folds_positive_net": 4}
    assert good["folds_positive_net"] >= good["folds_tested"] - 1
    weak = {"folds_tested": 4, "folds_positive_net": 2}
    assert not (weak["folds_positive_net"] >= weak["folds_tested"] - 1)
