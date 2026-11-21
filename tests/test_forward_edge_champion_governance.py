import base64
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from financial_brain.forward_edge.approvals import Approval
from financial_brain.forward_edge.champion_governance import (
    ChampionGovernanceError,
    ChampionGovernanceStore,
    ChampionPolicy,
)
from financial_brain.forward_edge.custody import Receipt, TrustRoot, receipt_message


NOW = datetime(2026, 11, 16, 4, tzinfo=timezone.utc)
MANIFEST = "c" * 64


def _approval(kind, subject, scope, *, actor="Ramesh"):
    return Approval(kind, subject, scope, actor, "human", NOW - timedelta(minutes=1),
                    NOW + timedelta(hours=1))


def _trust():
    private = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public = private.public_key().public_bytes_raw()
    root = TrustRoot("timestamp-provider", "key-1", base64.b64encode(public).decode("ascii"))
    anchored = NOW + timedelta(minutes=1)
    receipt = Receipt("timestamp-provider", "key-1", MANIFEST, anchored,
                      base64.b64encode(private.sign(receipt_message(
                          "timestamp-provider", "key-1", MANIFEST, anchored))).decode("ascii"))
    return receipt, {("timestamp-provider", "key-1"): root}


def _candidate():
    return ChampionPolicy("challenger-v2", "d" * 64, 0.02, 0.20)


def test_human_paper_champion_change_needs_scoped_approval_and_verified_receipt(tmp_path):
    store = ChampionGovernanceStore(tmp_path / "champion.sqlite3")
    safe = ChampionPolicy("naive-momentum", "a" * 64, 0.01, 0.10)
    assert store.initialize(safe, now=NOW).execution_mode == "SAFE_HOLD"
    candidate = _candidate()
    receipt, roots = _trust()
    state = store.change_paper_champion(
        transition_id="promote-1", policy=candidate, transition_type="PROMOTION",
        reason="human approved exact candidate after validation",
        strategy_approval=_approval("STRATEGY_ENROLLMENT", candidate.strategy_id,
                                    candidate.digest()),
        capital_approval=_approval("CAPITAL", candidate.strategy_id, candidate.digest()),
        evidence_manifest_sha256=MANIFEST, decision_at=NOW,
        earliest_entry_at=NOW + timedelta(minutes=5), receipt=receipt,
        trust_roots=roots, now=NOW + timedelta(minutes=10))
    assert state.policy == candidate
    assert state.execution_mode == "PAPER"
    assert state.entries_allowed
    assert not state.live_enabled
    assert store.events()[-1]["custody_status"] == "VERIFIED"
    store.close()


def test_champion_change_fails_without_external_custody_or_exact_human_approval(tmp_path):
    store = ChampionGovernanceStore(tmp_path / "champion.sqlite3")
    safe = ChampionPolicy("naive-momentum", "a" * 64, 0.01, 0.10)
    store.initialize(safe, now=NOW)
    candidate = _candidate()
    with pytest.raises(ChampionGovernanceError, match="LOCAL_ONLY"):
        store.change_paper_champion(
            transition_id="no-receipt", policy=candidate, transition_type="PROMOTION",
            reason="candidate", strategy_approval=_approval("STRATEGY_ENROLLMENT",
                candidate.strategy_id, candidate.digest()), evidence_manifest_sha256=MANIFEST,
            decision_at=NOW, earliest_entry_at=NOW + timedelta(minutes=5), receipt=None,
            trust_roots={}, now=NOW + timedelta(minutes=10))
    receipt, roots = _trust()
    with pytest.raises(ChampionGovernanceError, match="CAPITAL approval"):
        store.change_paper_champion(
            transition_id="no-capital-approval", policy=candidate, transition_type="PROMOTION",
            reason="candidate", strategy_approval=_approval("STRATEGY_ENROLLMENT",
                candidate.strategy_id, candidate.digest()), evidence_manifest_sha256=MANIFEST,
            decision_at=NOW, earliest_entry_at=NOW + timedelta(minutes=5), receipt=receipt,
            trust_roots=roots, now=NOW + timedelta(minutes=10))
    store.close()


@pytest.mark.parametrize("trigger", ("DRIFT", "OUTAGE", "ACCOUNTING_MISMATCH", "MANUAL_PAUSE"))
def test_automatic_rollback_enters_safe_hold_and_blocks_new_orders(tmp_path, trigger):
    path = tmp_path / "champion.sqlite3"
    store = ChampionGovernanceStore(path)
    safe = ChampionPolicy("safe-naive", "a" * 64, 0.01, 0.10)
    store.initialize(safe, now=NOW)
    receipt, roots = _trust()
    candidate = _candidate()
    store.change_paper_champion(
        transition_id="promote", policy=candidate, transition_type="PROMOTION", reason="approved",
        strategy_approval=_approval("STRATEGY_ENROLLMENT", candidate.strategy_id, candidate.digest()),
        capital_approval=_approval("CAPITAL", candidate.strategy_id, candidate.digest()),
        evidence_manifest_sha256=MANIFEST, decision_at=NOW,
        earliest_entry_at=NOW + timedelta(minutes=5), receipt=receipt, trust_roots=roots,
        now=NOW + timedelta(minutes=10))
    state = store.automatic_rollback(transition_id="rollback-1", trigger=trigger,
                                     now=NOW + timedelta(minutes=11),
                                     active_order_count=2, unknown_order_count=1)
    assert state.policy == safe
    assert state.execution_mode == "SAFE_HOLD"
    assert not state.entries_allowed
    assert not state.live_enabled
    assert store.events()[-1]["unknown_order_count"] == 1
    store.close()
    restarted = ChampionGovernanceStore(path)
    assert restarted.state() == state
    assert restarted.events()[-1]["trigger"] == trigger
    restarted.close()


def test_transition_log_is_hash_chained_and_sqlite_immutable(tmp_path):
    store = ChampionGovernanceStore(tmp_path / "champion.sqlite3")
    store.initialize(ChampionPolicy("safe", "a" * 64, 0.01, 0.10), now=NOW)
    event = store.events()[0]
    assert event["previous_event_sha256"] == "0" * 64
    with pytest.raises(sqlite3.IntegrityError, match="champion transitions are immutable"):
        store._con.execute("UPDATE champion_transitions SET event_json='{}' WHERE sequence=0")
    store.close()
