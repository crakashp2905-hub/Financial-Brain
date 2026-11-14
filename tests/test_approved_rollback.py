from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.config import Config
from financial_brain.forward_edge import approved_rollback as rollback
from financial_brain.forward_edge.approvals import Approval
from financial_brain.storage.db import Database


NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
FAMILY = "swing-momentum"


@pytest.fixture
def con(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as connection:
        yield connection


def _approval(kind: str, scope: str) -> Approval:
    return Approval(kind, FAMILY, scope, "owner:human-1", "human",
                    NOW - timedelta(minutes=1), NOW + timedelta(hours=1))


def _seed(con) -> None:
    for version, artifact in (("v1", "1" * 64), ("v2", "2" * 64)):
        scope = rollback.version_approval_scope_sha256(
            family_id=FAMILY, version_id=version, artifact_sha256=artifact,
            capital_inr=Decimal("100000"))
        rollback.approve_version(
            con, family_id=FAMILY, version_id=version, artifact_sha256=artifact,
            capital_inr=Decimal("100000"), approval=_approval("STRATEGY_ENROLLMENT", scope),
            now=NOW)


def test_alarm_requires_approval_to_rollback_and_reverts_only_to_preceding_version(con):
    _seed(con)
    rollback.record_alarm(con, family_id=FAMILY, version_id="v2", alarm_kind="HARM",
                          evidence_sha256="a" * 64, at_utc=NOW + timedelta(minutes=1))
    scope = rollback.rollback_scope_sha256(
        family_id=FAMILY, from_version="v2", to_version="v1",
        current_capital_inr=100000, requested_capital_inr=100000,
        alarm_evidence_sha256=("a" * 64,))
    with pytest.raises(rollback.RollbackError, match="rejected"):
        rollback.rollback_to_previous_approved(
            con, family_id=FAMILY, requested_capital_inr=100000,
            approval=_approval("STRATEGY_ENROLLMENT", scope), capital_approval=None,
            now=NOW + timedelta(minutes=2))
    row = rollback.rollback_to_previous_approved(
        con, family_id=FAMILY, requested_capital_inr=100000,
        approval=_approval("ROLLBACK", scope), capital_approval=None,
        now=NOW + timedelta(minutes=2))
    assert row["from_version"] == "v2" and row["to_version"] == "v1"
    assert rollback.active_version(con, family_id=FAMILY) == {
        "version_id": "v1", "artifact_sha256": "1" * 64, "capital_inr": "100000"}
    assert con.execute("SELECT COUNT(*) FROM approved_rollback_journal").fetchone()[0] == 4


def test_rollback_without_alarm_or_with_stale_scope_is_rejected(con):
    _seed(con)
    with pytest.raises(rollback.RollbackError, match="requires"):
        rollback.rollback_to_previous_approved(
            con, family_id=FAMILY, requested_capital_inr=100000,
            approval=_approval("ROLLBACK", "0" * 64), capital_approval=None, now=NOW)
    rollback.record_alarm(con, family_id=FAMILY, version_id="v2", alarm_kind="HARM",
                          evidence_sha256="c" * 64, at_utc=NOW + timedelta(minutes=1))
    stale_scope = rollback.rollback_scope_sha256(
        family_id=FAMILY, from_version="v2", to_version="v0",
        current_capital_inr=100000, requested_capital_inr=100000,
        alarm_evidence_sha256=("c" * 64,))
    with pytest.raises(rollback.RollbackError, match="rejected"):
        rollback.rollback_to_previous_approved(
            con, family_id=FAMILY, requested_capital_inr=100000,
            approval=_approval("ROLLBACK", stale_scope), capital_approval=None,
            now=NOW + timedelta(minutes=2))


def test_capital_change_requires_a_separate_scoped_human_approval(con):
    _seed(con)
    rollback.record_alarm(con, family_id=FAMILY, version_id="v2", alarm_kind="FORECAST_DRIFT",
                          evidence_sha256="b" * 64, at_utc=NOW + timedelta(minutes=1))
    scope = rollback.rollback_scope_sha256(
        family_id=FAMILY, from_version="v2", to_version="v1",
        current_capital_inr=100000, requested_capital_inr=50000,
        alarm_evidence_sha256=("b" * 64,))
    with pytest.raises(rollback.RollbackError, match="separate CAPITAL"):
        rollback.rollback_to_previous_approved(
            con, family_id=FAMILY, requested_capital_inr=50000,
            approval=_approval("ROLLBACK", scope), capital_approval=None,
            now=NOW + timedelta(minutes=2))
    capital_scope = rollback.rollback_capital_scope_sha256(
        rollback_scope=scope, current_capital_inr=100000, requested_capital_inr=50000)
    row = rollback.rollback_to_previous_approved(
        con, family_id=FAMILY, requested_capital_inr=50000,
        approval=_approval("ROLLBACK", scope),
        capital_approval=_approval("CAPITAL", capital_scope), now=NOW + timedelta(minutes=2))
    assert row["current_capital_inr"] == "100000"
    assert row["requested_capital_inr"] == "50000"
    assert rollback.active_version(con, family_id=FAMILY)["capital_inr"] == "50000"


def test_journal_detects_tampering(con):
    _seed(con)
    con.execute("UPDATE approved_rollback_journal SET record_json = replace(record_json, 'v1', 'vx') WHERE sequence = 1")
    with pytest.raises(rollback.RollbackError, match="hash chain"):
        rollback.active_version(con, family_id=FAMILY)
