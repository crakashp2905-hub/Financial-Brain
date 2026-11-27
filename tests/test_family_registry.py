from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from financial_brain.research.family_registry import (
    FamilyRegistryError, FamilyResearchRegistry, ReopenBasis, ReopenBasisKind,
)


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _basis(kind=ReopenBasisKind.NEW_INFORMATION, *, registered=T0 + timedelta(days=3),
           available=T0 + timedelta(days=2)):
    return ReopenBasis(kind, "new sourced corporate-action coverage", "a" * 64, registered, available)


def _fail(registry: FamilyResearchRegistry, family="momentum", attempt="a1", day=1):
    registered = T0 + timedelta(days=day)
    access = registered + timedelta(hours=1)
    registry.register_attempt(family_id=family, attempt_id=attempt, preregistration_sha256="b" * 64,
                              registered_at=registered, first_outcome_access_at=access)
    registry.record_result(family_id=family, attempt_id=attempt, verdict="FAIL", result_sha256="c" * 64,
                           recorded_at=access + timedelta(days=1))


def test_failed_family_stays_closed_until_new_basis_is_preregistered(tmp_path):
    registry = FamilyResearchRegistry(tmp_path / "families.sqlite")
    _fail(registry)
    assert registry.state("momentum") == "CLOSED_FAILED"
    with pytest.raises(FamilyRegistryError, match="requires new information"):
        registry.register_attempt(family_id="momentum", attempt_id="a2", preregistration_sha256="d" * 64,
                                  registered_at=T0 + timedelta(days=4),
                                  first_outcome_access_at=T0 + timedelta(days=5))
    basis = _basis(registered=T0 + timedelta(days=4), available=T0 + timedelta(days=3))
    registry.register_attempt(family_id="momentum", attempt_id="a2", preregistration_sha256="d" * 64,
                              registered_at=T0 + timedelta(days=4), first_outcome_access_at=T0 + timedelta(days=5),
                              basis=basis)
    assert registry.state("momentum") == "ATTEMPT_ACTIVE"
    registry.close()


def test_old_information_and_post_result_mechanism_cannot_reopen(tmp_path):
    registry = FamilyResearchRegistry(tmp_path / "families.sqlite")
    _fail(registry)
    old = _basis(registered=T0 + timedelta(days=4), available=T0 + timedelta(hours=12))
    with pytest.raises(FamilyRegistryError, match="new since"):
        registry.register_attempt(family_id="momentum", attempt_id="a2", preregistration_sha256="d" * 64,
                                  registered_at=T0 + timedelta(days=4), first_outcome_access_at=T0 + timedelta(days=5),
                                  basis=old)
    later_mechanism = _basis(ReopenBasisKind.NEW_PREREGISTERED_MECHANISM,
                             registered=T0 + timedelta(days=6), available=T0 + timedelta(days=6))
    with pytest.raises(FamilyRegistryError, match="before outcome access"):
        registry.register_attempt(family_id="momentum", attempt_id="a3", preregistration_sha256="d" * 64,
                                  registered_at=T0 + timedelta(days=4), first_outcome_access_at=T0 + timedelta(days=5),
                                  basis=later_mechanism)
    registry.close()


def test_attempt_grid_is_fixed_and_every_failed_reopening_recloses_family(tmp_path):
    registry = FamilyResearchRegistry(tmp_path / "families.sqlite")
    _fail(registry)
    basis = _basis(registered=T0 + timedelta(days=4), available=T0 + timedelta(days=3))
    registry.register_attempt(family_id="momentum", attempt_id="a2", preregistration_sha256="d" * 64,
                              registered_at=T0 + timedelta(days=4), first_outcome_access_at=T0 + timedelta(days=5),
                              basis=basis)
    registry.record_result(family_id="momentum", attempt_id="a2", verdict="NO_EDGE",
                           result_sha256="e" * 64, recorded_at=T0 + timedelta(days=6))
    assert registry.state("momentum") == "CLOSED_FAILED"
    with pytest.raises(FamilyRegistryError, match="requires new information"):
        registry.register_attempt(family_id="momentum", attempt_id="a3", preregistration_sha256="f" * 64,
                                  registered_at=T0 + timedelta(days=7), first_outcome_access_at=T0 + timedelta(days=8))
    registry.close()


def test_one_active_attempt_and_result_identity_are_required(tmp_path):
    registry = FamilyResearchRegistry(tmp_path / "families.sqlite")
    registry.register_attempt(family_id="new", attempt_id="a1", preregistration_sha256="b" * 64,
                              registered_at=T0, first_outcome_access_at=T0 + timedelta(days=1))
    with pytest.raises(FamilyRegistryError, match="active registered attempt"):
        registry.register_attempt(family_id="new", attempt_id="a2", preregistration_sha256="b" * 64,
                                  registered_at=T0, first_outcome_access_at=T0 + timedelta(days=1))
    with pytest.raises(FamilyRegistryError, match="differs from the active"):
        registry.record_result(family_id="new", attempt_id="a2", verdict="FAIL", result_sha256="c" * 64,
                               recorded_at=T0 + timedelta(days=2))
    registry.close()


def test_registry_hash_chain_survives_reopen_and_detects_tampering(tmp_path):
    path = tmp_path / "families.sqlite"
    registry = FamilyResearchRegistry(path)
    _fail(registry)
    registry.close()
    registry = FamilyResearchRegistry(path)
    assert registry.audit()["integrity"] == "VALID"
    registry._con.execute("UPDATE family_events SET body_json='{}' WHERE sequence=1")
    with pytest.raises(FamilyRegistryError, match="hash chain"):
        registry.audit()
    registry.close()


def test_no_replicable_alpha_verdict_is_terminal_and_requires_new_basis(tmp_path):
    registry = FamilyResearchRegistry(tmp_path / "families.sqlite")
    attempt_at = T0 + timedelta(days=1)
    access_at = attempt_at + timedelta(hours=1)
    registry.register_attempt(family_id="events", attempt_id="search-1",
                              preregistration_sha256="a" * 64,
                              registered_at=attempt_at, first_outcome_access_at=access_at)
    registry.record_result(family_id="events", attempt_id="search-1",
                           verdict="NO_REPLICABLE_ALPHA_FOUND", result_sha256="b" * 64,
                           recorded_at=access_at + timedelta(days=1))
    assert registry.state("events") == "CLOSED_FAILED"
    with pytest.raises(FamilyRegistryError, match="closed family requires"):
        registry.register_attempt(family_id="events", attempt_id="search-2",
                                  preregistration_sha256="c" * 64,
                                  registered_at=T0 + timedelta(days=5),
                                  first_outcome_access_at=T0 + timedelta(days=6))
    assert registry.audit()["integrity"] == "VALID"
    registry.close()


def test_concurrent_registration_cannot_create_two_active_attempts(tmp_path):
    path = tmp_path / "concurrent.sqlite"
    setup = FamilyResearchRegistry(path)
    setup.close()
    barrier = Barrier(2)

    def register(attempt):
        registry = FamilyResearchRegistry(path)
        try:
            barrier.wait()
            return registry.register_attempt(
                family_id="same-family", attempt_id=attempt, preregistration_sha256="b" * 64,
                registered_at=T0, first_outcome_access_at=T0 + timedelta(hours=1),
            )
        except FamilyRegistryError as exc:
            return str(exc)
        finally:
            registry.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(register, ("a1", "a2")))
    assert sum(isinstance(result, str) and len(result) == 64 for result in results) == 1
    assert sum(result == "family already has an active registered attempt" for result in results) == 1
    resumed = FamilyResearchRegistry(path)
    assert resumed.state("same-family") == "ATTEMPT_ACTIVE"
    assert resumed.audit()["events"] == 1
    resumed.close()
