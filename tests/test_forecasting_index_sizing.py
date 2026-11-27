import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from financial_brain.forward_edge.custody import Receipt, TrustRoot, receipt_message
from financial_brain.forecasting.index_sizing import (
    ForecastSizingError,
    ProspectiveSizingEvidence,
    confidence_adjusted_paper_lots,
    evaluate_prospective_sizing_evidence,
)

NOW = datetime(2026, 11, 27, 10, tzinfo=timezone.utc)
SAMPLE = "a" * 64
MODEL = "b" * 64


def receipt():
    private = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public = private.public_key().public_bytes_raw()
    root = TrustRoot("independent-tsa", "key-1", base64.b64encode(public).decode())
    anchored = NOW - timedelta(minutes=2)
    signed = Receipt("independent-tsa", "key-1", SAMPLE, anchored,
                     base64.b64encode(private.sign(receipt_message(
                         "independent-tsa", "key-1", SAMPLE, anchored))).decode())
    return signed, {("independent-tsa", "key-1"): root}


def evidence(**changes):
    signed, roots = receipt()
    values = {"strategy_id": "index-options-paper", "model_sha256": MODEL,
              "sample_manifest_sha256": SAMPLE,
              "sample_finalized_at": NOW - timedelta(minutes=5), "option_decision_at": NOW,
              "prospective_sessions": 80, "minimum_sessions": 60,
              "predictive_skill_status": "PREDICTIVE_SKILL_VERIFIED",
              "calibration_error_upper_95": .08, "maximum_calibration_error": .10,
              "receipt": signed, "trust_roots": roots}
    values.update(changes)
    return ProspectiveSizingEvidence(**values)


def test_only_verified_prospective_skill_and_calibration_allow_confidence_sizing():
    approved = evaluate_prospective_sizing_evidence(evidence())
    assert approved.eligible and approved.custody_status == "VERIFIED"
    sized = confidence_adjusted_paper_lots(base_risk_capped_lots=10,
                                           calibrated_confidence=.75, eligibility=approved)
    assert sized == {"status": "PAPER_SIZE", "lots": 7,
                     "reason": "CALIBRATED_CONFIDENCE_SCALED_RISK_CAP",
                     "evidence_sha256": SAMPLE}


@pytest.mark.parametrize("overrides,reason", [
    ({"prospective_sessions": 20}, "INSUFFICIENT_INDEPENDENT_PROSPECTIVE_SESSIONS"),
    ({"predictive_skill_status": "NOT_VERIFIED"}, "PREDICTIVE_SKILL_GATE_NOT_PASSED"),
    ({"calibration_error_upper_95": .2}, "CONFIDENCE_NOT_CALIBRATED_TO_POLICY"),
    ({"receipt": None}, "INDEPENDENT_CUSTODY_NOT_VERIFIED"),
    ({"sample_finalized_at": NOW}, "SAMPLE_NOT_FINALIZED_BEFORE_OPTION_DECISION"),
])
def test_missing_independent_prospective_evidence_disables_confidence_sizing(overrides, reason):
    result = evaluate_prospective_sizing_evidence(evidence(**overrides))
    assert not result.eligible and reason in result.reasons
    sizing = confidence_adjusted_paper_lots(base_risk_capped_lots=10,
                                            calibrated_confidence=.99, eligibility=result)
    assert sizing["status"] == "NO_TRADE" and sizing["lots"] == 0


def test_confidence_sizing_validates_confidence_and_never_exceeds_risk_cap():
    approved = evaluate_prospective_sizing_evidence(evidence())
    sized = confidence_adjusted_paper_lots(base_risk_capped_lots=4,
                                           calibrated_confidence=1.0, eligibility=approved)
    assert sized["lots"] == 4
    with pytest.raises(ForecastSizingError, match=r"in \[0, 1\]"):
        confidence_adjusted_paper_lots(base_risk_capped_lots=4,
                                       calibrated_confidence=1.1, eligibility=approved)
