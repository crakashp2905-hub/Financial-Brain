from datetime import datetime, timezone

import pytest

from financial_brain.research.family_registration import (
    EvidenceDisposition, FamilyEvaluation, FamilyRegistration, FamilyRegistrationError, PriorClass,
)


def _registration(at: datetime | None = None, *, family: int = 20) -> FamilyRegistration:
    return FamilyRegistration("reg-1", "hyp-1", PriorClass.B, family,
                              at or datetime(2026, 1, 1, tzinfo=timezone.utc), "a" * 64)


def test_prior_class_and_family_must_precede_first_outcome_access() -> None:
    ev = FamilyEvaluation(_registration(), datetime(2026, 2, 1, tzinfo=timezone.utc),
                          datetime(2026, 2, 2, tzinfo=timezone.utc), "b" * 64)
    assert ev.disposition is EvidenceDisposition.FORWARD_REGISTERED
    assert ev.may_certify_forward
    assert ev.audit_record()["registration"]["family_size"] == 20


@pytest.mark.parametrize("registration_time", [
    datetime(2026, 2, 1, tzinfo=timezone.utc),
    datetime(2026, 3, 1, tzinfo=timezone.utc),
])
def test_same_day_or_post_outcome_registration_is_descriptive_only(registration_time: datetime) -> None:
    ev = FamilyEvaluation(_registration(registration_time), datetime(2026, 2, 1, tzinfo=timezone.utc),
                          datetime(2026, 3, 2, tzinfo=timezone.utc), "b" * 64)
    assert ev.disposition is EvidenceDisposition.RETROSPECTIVE_ONLY
    assert not ev.may_certify_forward


@pytest.mark.parametrize("family", [0, -1, True, 1.5])
def test_family_size_is_a_positive_frozen_integer(family: object) -> None:
    with pytest.raises(FamilyRegistrationError, match="positive integer"):
        _registration(family=family)  # type: ignore[arg-type]


def test_naive_registration_clock_is_rejected() -> None:
    with pytest.raises(FamilyRegistrationError, match="timezone-aware"):
        _registration(datetime(2026, 1, 1))


def test_score_cannot_precede_outcome_access() -> None:
    with pytest.raises(FamilyRegistrationError, match="cannot precede"):
        FamilyEvaluation(_registration(), datetime(2026, 2, 1, tzinfo=timezone.utc),
                         datetime(2026, 1, 31, tzinfo=timezone.utc), "b" * 64)
