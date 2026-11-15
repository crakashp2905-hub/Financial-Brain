from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from financial_brain.forward_edge.approvals import Approval
from financial_brain.forward_edge.persistent_killswitch import (
    KillSwitchError,
    PersistentKillSwitch,
)


def _time():
    return datetime(2026, 11, 16, 10, tzinfo=timezone.utc)


def _approval(switch, *, actor="CR-Ramesh", actor_type="human", now=None):
    now = now or _time()
    return Approval("DISARM_RESET", "paper-entry", switch.reset_scope_hash(), actor, actor_type,
                    now - timedelta(minutes=1), now + timedelta(hours=1), "ADR-RESET")


def test_daily_loss_feed_and_reconciliation_latch_survives_restart(tmp_path) -> None:
    path = tmp_path / "kill.sqlite3"
    switch = PersistentKillSwitch(path)
    switch.trip(event_id="loss-1", reason="DAILY_LOSS", occurred_at=_time(), evidence_hash="loss-evidence")
    assert switch.trip(event_id="loss-1", reason="DAILY_LOSS", occurred_at=_time(), evidence_hash="loss-evidence") is False
    switch.trip(event_id="feed-1", reason="STALE_FEED", occurred_at=_time(), evidence_hash="feed-evidence")
    switch.trip(event_id="ledger-1", reason="RECONCILIATION_MISMATCH", occurred_at=_time(), evidence_hash="ledger-evidence")
    assert switch.active_reasons == ("DAILY_LOSS", "STALE_FEED", "RECONCILIATION_MISMATCH")
    switch.close()
    restored = PersistentKillSwitch(path)
    assert restored.active
    assert "STALE_FEED" in restored.active_reasons
    restored.close()


def test_llm_agent_cannot_reset_and_human_reset_is_scope_bound(tmp_path) -> None:
    switch = PersistentKillSwitch(tmp_path / "kill.sqlite3")
    switch.trip(event_id="dd-1", reason="DRAWDOWN", occurred_at=_time(), evidence_hash="dd")
    approval = _approval(switch, actor="agent:codex")
    with pytest.raises(KillSwitchError, match="approval rejected"):
        switch.reset(event_id="reset-bad", approval=approval, now=_time(), evidence_hash="human-check")
    valid = _approval(switch)
    switch.trip(event_id="new-trip", reason="STALE_FEED", occurred_at=_time(), evidence_hash="new-feed")
    with pytest.raises(KillSwitchError, match="stale approval"):
        switch.reset(event_id="reset-stale", approval=valid, now=_time(), evidence_hash="check")
    current = _approval(switch)
    assert switch.reset(event_id="reset-ok", approval=current, now=_time(), evidence_hash="reconciled")
    assert not switch.active
    switch.close()


def test_reset_survives_restart_and_event_rows_are_immutable(tmp_path) -> None:
    path = tmp_path / "kill.sqlite3"
    switch = PersistentKillSwitch(path)
    switch.trip(event_id="stale", reason="STALE_FEED", occurred_at=_time(), evidence_hash="source")
    switch.reset(event_id="reset", approval=_approval(switch), now=_time(), evidence_hash="fresh-feed")
    assert not switch.active
    switch.close()
    with sqlite3.connect(path) as con:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            con.execute("UPDATE kill_events SET event_json='{}' WHERE event_id='stale'")
    restored = PersistentKillSwitch(path)
    assert not restored.active
    restored.trip(event_id="later-loss", reason="DAILY_LOSS", occurred_at=_time(), evidence_hash="later")
    assert restored.active
    restored.close()


def test_heartbeat_broker_unknown_and_risk_breach_reasons_latch_until_human_reset(tmp_path) -> None:
    switch = PersistentKillSwitch(tmp_path / "kill.sqlite3")
    for index, reason in enumerate(("LOST_HEARTBEAT", "BROKER_STATE_UNKNOWN", "RISK_LIMIT_BREACH")):
        switch.trip(event_id=f"safety-{index}", reason=reason, occurred_at=_time(),
                    evidence_hash=f"evidence-{index}")
    assert set(switch.active_reasons) == {
        "LOST_HEARTBEAT", "BROKER_STATE_UNKNOWN", "RISK_LIMIT_BREACH"}
    approval = _approval(switch)
    assert switch.reset(event_id="human-reset-safety", approval=approval, now=_time(),
                        evidence_hash="post-reconciliation-evidence")
    assert not switch.active
    switch.close()
