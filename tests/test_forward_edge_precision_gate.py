from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.precision_gate import (
    PrecisionGateError,
    PrecisionPlan,
    SessionEligibility,
    decide_precision_stop,
)


def _plan(maximum=90):
    return PrecisionPlan("plan-1", datetime(2026, 11, 15, 10, tzinfo=timezone.utc),
                         date(2026, 11, 16), 60, maximum, 0.01, 0.95)


def _sessions(count, excluded=0):
    start = date(2026, 11, 16)
    return tuple(SessionEligibility(start + timedelta(days=i), i >= excluded) for i in range(count))


def test_provisional_60_session_floor_never_promotes_automatically() -> None:
    result = decide_precision_stop(_plan(), _sessions(60), ci_lower=0.02, ci_upper=0.03,
                                   interval_confidence_level=0.95)
    assert result.eligible_sessions == 60
    assert result.decision == "REVIEW_ONLY"
    assert not result.promotion_allowed
    assert "not proof" in result.reason


def test_insufficient_sample_continues_and_precision_at_max_is_inconclusive() -> None:
    early = decide_precision_stop(_plan(), _sessions(59), ci_lower=-0.01, ci_upper=0.01,
                                  interval_confidence_level=0.95)
    assert early.decision == "CONTINUE"
    at_max = decide_precision_stop(_plan(60), _sessions(60), ci_lower=-0.03, ci_upper=0.03,
                                   interval_confidence_level=0.95)
    assert at_max.decision == "INCONCLUSIVE_AT_MAX"


def test_exclusions_count_and_plan_contract_cannot_be_violated() -> None:
    result = decide_precision_stop(_plan(), _sessions(65, excluded=5), ci_lower=0.0, ci_upper=0.005,
                                   interval_confidence_level=0.95)
    assert result.eligible_sessions == 60
    assert result.excluded_sessions == 5
    with pytest.raises(PrecisionGateError, match="at least"):
        PrecisionPlan("too-small", datetime(2026, 11, 15, tzinfo=timezone.utc),
                      date(2026, 11, 16), 30, 60, 0.01, 0.95)
    with pytest.raises(PrecisionGateError, match="differs"):
        decide_precision_stop(_plan(), _sessions(60), ci_lower=0, ci_upper=0.001,
                              interval_confidence_level=0.90)
