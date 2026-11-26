from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.approvals import Approval
from financial_brain.research.holdout_access import (
    HoldoutAccessError, HoldoutAccessLedger, holdout_scope_sha256,
)


NOW = datetime(2026, 11, 26, 10, tzinfo=timezone.utc)
OWNER = "owner:ramesh"


def _approval(dataset: str, run: str, *, owner: str = OWNER, dataset_hash: str = "a" * 64,
              prereg: str = "b" * 64, actor_type: str = "human") -> Approval:
    scope = holdout_scope_sha256(dataset_id=dataset, dataset_sha256=dataset_hash,
                                 run_id=run, preregistration_sha256=prereg)
    return Approval("HOLDOUT_ACCESS", run, scope, owner, actor_type, NOW - timedelta(minutes=1),
                    NOW + timedelta(hours=1), "ADR-0007")


def test_owner_approval_consumes_holdout_once_and_persists_chain(tmp_path) -> None:
    ledger = HoldoutAccessLedger(tmp_path / "holdouts.sqlite")
    receipt = ledger.consume(dataset_id="holdout-2026", dataset_sha256="a" * 64,
                            run_id="run-001", preregistration_sha256="b" * 64,
                            authorized_owner_id=OWNER, approval=_approval("holdout-2026", "run-001"), now=NOW)
    assert receipt.event_hash
    assert ledger.audit()["consumed_datasets"] == 1
    with pytest.raises(HoldoutAccessError, match="already been consumed"):
        ledger.consume(dataset_id="holdout-2026", dataset_sha256="a" * 64,
                       run_id="run-002", preregistration_sha256="c" * 64,
                       authorized_owner_id=OWNER, approval=_approval("holdout-2026", "run-002",
                                                                       dataset_hash="a" * 64, prereg="c" * 64),
                       now=NOW)
    ledger.close()
    reopened = HoldoutAccessLedger(tmp_path / "holdouts.sqlite")
    assert reopened.audit()["integrity"] == "VALID"
    reopened.close()


def test_non_owner_and_ai_approval_are_rejected(tmp_path) -> None:
    ledger = HoldoutAccessLedger(tmp_path / "holdouts.sqlite")
    with pytest.raises(HoldoutAccessError, match="configured owner"):
        ledger.consume(dataset_id="d", dataset_sha256="a" * 64, run_id="r",
                       preregistration_sha256="b" * 64, authorized_owner_id=OWNER,
                       approval=_approval("d", "r", owner="reviewer:other"), now=NOW)
    with pytest.raises(HoldoutAccessError, match="agent or non-human"):
        ledger.consume(dataset_id="d", dataset_sha256="a" * 64, run_id="r",
                       preregistration_sha256="b" * 64, authorized_owner_id="agent:owner",
                       approval=_approval("d", "r", owner="agent:owner", actor_type="agent"), now=NOW)
    ledger.close()


def test_stale_scope_expired_approval_and_run_reuse_fail_closed(tmp_path) -> None:
    ledger = HoldoutAccessLedger(tmp_path / "holdouts.sqlite")
    approval = _approval("d", "r")
    with pytest.raises(HoldoutAccessError, match="rejected"):
        ledger.consume(dataset_id="d", dataset_sha256="c" * 64, run_id="r",
                       preregistration_sha256="b" * 64, authorized_owner_id=OWNER,
                       approval=approval, now=NOW)
    expired = Approval("HOLDOUT_ACCESS", "r", approval.scope_sha256, OWNER, "human",
                       NOW - timedelta(hours=2), NOW - timedelta(hours=1), "ADR-0007")
    with pytest.raises(HoldoutAccessError, match="expired"):
        ledger.consume(dataset_id="d", dataset_sha256="a" * 64, run_id="r",
                       preregistration_sha256="b" * 64, authorized_owner_id=OWNER,
                       approval=expired, now=NOW)
    ledger.consume(dataset_id="d1", dataset_sha256="a" * 64, run_id="r",
                   preregistration_sha256="b" * 64, authorized_owner_id=OWNER,
                   approval=_approval("d1", "r"), now=NOW)
    with pytest.raises(HoldoutAccessError, match="already been consumed"):
        ledger.consume(dataset_id="d2", dataset_sha256="a" * 64, run_id="r",
                       preregistration_sha256="b" * 64, authorized_owner_id=OWNER,
                       approval=_approval("d2", "r"), now=NOW)
    ledger.close()
