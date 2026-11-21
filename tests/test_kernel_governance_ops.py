from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest

from financial_brain.kernel.governance import (
    AuditLog,
    AuthzError,
    Tool,
    accept_numeric_output,
    authorize,
    invoke,
    scrub,
)
from financial_brain.kernel.ops import Alert, AlertQueue, restore, snapshot

T = datetime(2026, 11, 9, 10, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))


def test_researcher_cannot_submit_or_reset() -> None:
    authorize("trader", "submit_paper")
    with pytest.raises(AuthzError):
        authorize("researcher", "submit_paper")
    with pytest.raises(AuthzError):
        authorize("researcher", "reset_latch")


def test_unknown_role_has_no_authority() -> None:
    with pytest.raises(AuthzError, match="no authority"):
        authorize("contractor", "read")


def test_audit_chain_detects_edit_and_deletion() -> None:
    log = AuditLog()
    log.append("owner", "reset_latch", {"reason": "reviewed"})
    log.append("trader", "submit_paper", {"order": "c1"})
    assert log.verify()
    log.records[0]["detail"]["reason"] = "rewritten"
    assert not log.verify()
    log2 = AuditLog()
    log2.append("a", "x", {})
    log2.append("a", "y", {})
    del log2.records[0]
    assert not log2.verify()


def test_scrubber_removes_secret_shaped_values() -> None:
    text = "api_key=ABCDEF123 and token: xyz and plain text"
    cleaned = scrub(text)
    assert "ABCDEF123" not in cleaned and "xyz" not in cleaned and "plain text" in cleaned


def test_read_only_agent_cannot_invoke_write_tool() -> None:
    with pytest.raises(AuthzError):
        invoke(Tool("place_paper_order", "WRITE"), agent_tier="READ")
    invoke(Tool("get_quote", "READ"), agent_tier="READ")


def test_model_sizing_must_be_finite_and_in_range() -> None:
    assert accept_numeric_output(100, low=0, high=1000) == 100.0
    for bad in (float("nan"), 1e9, -1, True, "100"):
        with pytest.raises(ValueError):
            accept_numeric_output(bad, low=0, high=1000)


def test_alert_dedupes_exactly_once() -> None:
    q = AlertQueue()
    a = Alert("k1", "BREAKOUT", T, "ABC broke out")
    assert q.offer(a) == "DELIVERED"
    assert q.offer(a) == "DUPLICATE"
    assert len(q.delivered) == 1


def test_stale_feed_blocks_breakout_alert() -> None:
    q = AlertQueue()
    assert q.offer(Alert("k2", "BREAKOUT", T, "x"), feed_fresh=False) == "STALE"


def test_throttle_and_quiet_hours_but_critical_pierces() -> None:
    q = AlertQueue()
    q.offer(Alert("k3", "BREAKOUT", T, "a"))
    assert q.offer(Alert("k4", "BREAKOUT", T + timedelta(seconds=10), "b")) == "THROTTLED"
    night = datetime(2026, 11, 9, 23, 30, tzinfo=T.tzinfo)
    assert q.offer(Alert("k5", "BREAKOUT", night, "c")) == "QUIET"
    assert q.offer(Alert("k6", "MARGIN", night, "margin call")) == "DELIVERED"
    assert q.quiet_start == time(22, 0)


def test_restore_verifies_and_refuses_tampered_manifest() -> None:
    tables = {"orders": [{"id": "c1", "qty": 10}], "ledger": [{"txn": "t1", "amt": "100"}]}
    manifest = snapshot(tables)
    assert restore(manifest) == tables
    manifest["tables"]["ledger"]["rows"][0]["amt"] = "999"
    with pytest.raises(ValueError, match="refused"):
        restore(manifest)
