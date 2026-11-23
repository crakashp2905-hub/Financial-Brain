from datetime import timedelta

import pytest

from financial_brain.signals.assessment import (AssessmentError, FeatureEvidence,
                                                 ReturnDistribution, SignalAssessment)
from test_signal_contract import HASH, NOW, make_signal


def test_assessment_reports_uncertainty_benchmark_cost_risk_and_net_return():
    signal = make_signal()
    assessment = SignalAssessment(
        signal.signal_id,
        (FeatureEvidence("momentum_20d", 0.12, NOW - timedelta(minutes=2), HASH),),
        ReturnDistribution(5, -0.03, 0.04, 0.09), "NIFTY_500_TRI", 0.02, 25, 0.01,
        ("earnings_within_24h",))
    assessment.validate_for(signal)
    assert assessment.expected_net_return == pytest.approx(0.0375)


def test_assessment_rejects_lookahead_and_bad_uncertainty_order():
    signal = make_signal()
    assessment = SignalAssessment(
        signal.signal_id,
        (FeatureEvidence("future_close", 1, NOW + timedelta(seconds=1), HASH),),
        ReturnDistribution(5, -0.1, 0, 0.1), "NIFTY_500_TRI", 0, 0, 0)
    with pytest.raises(AssessmentError, match="unavailable"):
        assessment.validate_for(signal)
    with pytest.raises(AssessmentError, match="ordered"):
        ReturnDistribution(5, 0.2, 0.1, 0.3)
