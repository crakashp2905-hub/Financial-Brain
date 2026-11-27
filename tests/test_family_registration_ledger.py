from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.research.family_registration import (
    EvidenceDisposition,
    FamilyRegistrationError,
    FamilyRegistrationLedger,
    PriorClass,
)


class FrozenClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def test_ledger_timestamps_registration_and_binds_future_outcome_once(tmp_path) -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    ledger = FamilyRegistrationLedger(tmp_path / "families.sqlite", clock=clock)
    registration = ledger.register(hypothesis_id="H-1", prior_class=PriorClass.B,
                                   family_size=17, preregistration_sha256="a" * 64)
    clock.value += timedelta(days=1)
    first_access = ledger.record_outcome_access(
        registration.registration_id, dataset_sha256="b" * 64,
        outcome_available_at=clock.value,
    )
    clock.value += timedelta(days=1)
    result = ledger.record_score(registration.registration_id, result_sha256="c" * 64)
    assert result.first_outcome_access_at == first_access
    assert result.disposition is EvidenceDisposition.FORWARD_REGISTERED
    assert result.audit_record()["registration"]["family_size"] == 17
    with pytest.raises(FamilyRegistrationError, match="only once"):
        ledger.record_score(registration.registration_id, result_sha256="d" * 64)
    assert ledger.audit()["integrity"] == "VALID"
    ledger.close()


def test_historical_dataset_remains_retrospective_even_if_accessed_after_registration(tmp_path) -> None:
    clock = FrozenClock(datetime(2026, 2, 1, tzinfo=timezone.utc))
    ledger = FamilyRegistrationLedger(tmp_path / "families.sqlite", clock=clock)
    registration = ledger.register(hypothesis_id="H-historical", prior_class=PriorClass.C,
                                   family_size=100, preregistration_sha256="a" * 64)
    clock.value += timedelta(days=1)
    ledger.record_outcome_access(registration.registration_id, dataset_sha256="b" * 64,
                                 outcome_available_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    clock.value += timedelta(days=1)
    result = ledger.record_score(registration.registration_id, result_sha256="c" * 64)
    assert result.disposition is EvidenceDisposition.RETROSPECTIVE_ONLY
    assert not result.may_certify_forward
    assert ledger.audit()["integrity"] == "VALID"
    ledger.close()


def test_ledger_rejects_future_availability_and_repeated_first_access(tmp_path) -> None:
    clock = FrozenClock(datetime(2026, 3, 1, tzinfo=timezone.utc))
    ledger = FamilyRegistrationLedger(tmp_path / "families.sqlite", clock=clock)
    registration = ledger.register(hypothesis_id="H-2", prior_class=PriorClass.A,
                                   family_size=1, preregistration_sha256="a" * 64)
    with pytest.raises(FamilyRegistrationError, match="after first access"):
        ledger.record_outcome_access(registration.registration_id, dataset_sha256="b" * 64,
                                     outcome_available_at=clock.value + timedelta(days=1))
    clock.value += timedelta(days=1)
    ledger.record_outcome_access(registration.registration_id, dataset_sha256="b" * 64,
                                 outcome_available_at=clock.value)
    with pytest.raises(FamilyRegistrationError, match="already recorded"):
        ledger.record_outcome_access(registration.registration_id, dataset_sha256="d" * 64,
                                     outcome_available_at=clock.value)
    ledger.close()


def test_registration_and_score_lifecycle_survives_restart(tmp_path) -> None:
    clock = FrozenClock(datetime(2026, 4, 1, tzinfo=timezone.utc))
    path = tmp_path / "families.sqlite"
    ledger = FamilyRegistrationLedger(path, clock=clock)
    registration = ledger.register(hypothesis_id="H-3", prior_class=PriorClass.B,
                                   family_size=5, preregistration_sha256="a" * 64)
    clock.value += timedelta(days=1)
    ledger.record_outcome_access(registration.registration_id, dataset_sha256="b" * 64,
                                 outcome_available_at=clock.value)
    ledger.close()
    resumed = FamilyRegistrationLedger(path, clock=clock)
    clock.value += timedelta(days=1)
    result = resumed.record_score(registration.registration_id, result_sha256="c" * 64)
    assert result.may_certify_forward
    assert resumed.audit()["events"] == 3
    resumed.close()


def test_outcome_cannot_be_scored_without_registered_access(tmp_path) -> None:
    clock = FrozenClock(datetime(2026, 5, 1, tzinfo=timezone.utc))
    ledger = FamilyRegistrationLedger(tmp_path / "families.sqlite", clock=clock)
    registration = ledger.register(hypothesis_id="H-4", prior_class=PriorClass.A,
                                   family_size=2, preregistration_sha256="a" * 64)
    with pytest.raises(FamilyRegistrationError, match="before outcome access"):
        ledger.record_score(registration.registration_id, result_sha256="c" * 64)
    ledger.close()
