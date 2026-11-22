from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.mf_paper_realism import (
    MFPaperRealismAssessment, MFRealismBasis, MFRealismDimension,
    MFRealismError, MFRealismFactor,
)


NOW = datetime(2026, 11, 23, 8, tzinfo=timezone.utc)


def _factor(dimension, value, **changes):
    values = {
        "dimension": dimension, "value": Decimal(value),
        "basis": MFRealismBasis.SIMULATED_ASSUMPTION,
        "assessed_at": NOW, "rationale": "frozen simulation evidence",
    }
    return MFRealismFactor(**(values | changes))


def test_realism_score_is_weighted_and_reports_assessment_and_observed_coverage():
    factors = (
        _factor(MFRealismDimension.ELIGIBLE_NAV_CERTAINTY, "1"),
        _factor(MFRealismDimension.PAYMENT_TIME_EVIDENCE, "0.5"),
    )
    assessment = MFPaperRealismAssessment("paper-account", NOW, factors)
    assert assessment.score == Decimal("47.50")
    assert assessment.evidence_coverage == Decimal("60.00")
    assert assessment.observed_coverage == Decimal("0")
    assert assessment.missing_dimensions == (
        MFRealismDimension.LOAD_COMPLETENESS,
        MFRealismDimension.REDEMPTION_SETTLEMENT_CONFIDENCE,
    )
    assert len(assessment.assessment_sha256) == 64


def test_missing_dimensions_contribute_zero_instead_of_renormalizing_score():
    assessment = MFPaperRealismAssessment(
        "paper-account", NOW,
        (_factor(MFRealismDimension.ELIGIBLE_NAV_CERTAINTY, "1"),),
    )
    assert assessment.score == Decimal("35.00")
    assert assessment.evidence_coverage == Decimal("35.00")


def test_observed_dimension_requires_hash_and_is_separately_counted():
    observed = _factor(
        MFRealismDimension.LOAD_COMPLETENESS, "0.8",
        basis=MFRealismBasis.OBSERVED_EVIDENCE,
        evidence_ref="terms-snapshot", source_id="AMC_NOTICE",
        source_sha256="d" * 64,
    )
    assessment = MFPaperRealismAssessment("paper-account", NOW, (observed,))
    assert assessment.score == Decimal("16.00")
    assert assessment.evidence_coverage == Decimal("20.00")
    assert assessment.observed_coverage == Decimal("20.00")
    with pytest.raises(MFRealismError, match="observed factor requires"):
        _factor(MFRealismDimension.LOAD_COMPLETENESS, "0.8",
                basis=MFRealismBasis.OBSERVED_EVIDENCE)


@pytest.mark.parametrize("value", ["-0.01", "1.01", "NaN", "Infinity"])
def test_factor_values_must_be_finite_and_in_unit_interval(value):
    with pytest.raises(MFRealismError, match=r"in \[0, 1\]"):
        _factor(MFRealismDimension.ELIGIBLE_NAV_CERTAINTY, value)


def test_duplicate_and_future_factors_are_rejected():
    factor = _factor(MFRealismDimension.ELIGIBLE_NAV_CERTAINTY, "0.7")
    with pytest.raises(MFRealismError, match="only once"):
        MFPaperRealismAssessment("paper-account", NOW, (factor, factor))
    future = _factor(MFRealismDimension.PAYMENT_TIME_EVIDENCE, "0.5",
                     assessed_at=NOW + timedelta(seconds=1))
    with pytest.raises(MFRealismError, match="future-dated"):
        MFPaperRealismAssessment("paper-account", NOW, (future,))
