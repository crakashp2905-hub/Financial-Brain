from datetime import date, datetime, timezone

import pytest

from financial_brain.forward_edge.shadow import (
    SHADOW_FAMILIES,
    ShadowLogError,
    ShadowOpportunity,
    ShadowOpportunityLog,
    ShadowScan,
)


def test_shadow_log_captures_every_family_scan_including_no_signal_and_no_outcomes(tmp_path) -> None:
    log = ShadowOpportunityLog(tmp_path / "shadow.sqlite3")
    now = datetime(2026, 11, 16, 4, tzinfo=timezone.utc)
    for family in SHADOW_FAMILIES:
        opportunities = () if family == "orb" else (
            ShadowOpportunity(f"{family}-1", "NSE:ABC", "BUY", "SETUP_A", "event-hash"),)
        log.record_scan(ShadowScan("session-1", date(2026, 11, 16), family, now,
                                   "snapshot-hash", opportunities))
    coverage = log.session_coverage("session-1")
    assert coverage["complete"]
    assert coverage["missing_families"] == []
    assert coverage["families"]["orb"] == 0
    rows = log._con.execute("SELECT scan_json FROM shadow_scans").fetchall()
    assert all("pnl" not in row[0].lower() and "outcome" not in row[0].lower() for row in rows)
    log.close()


def test_shadow_scan_is_idempotent_and_conflicts_are_rejected(tmp_path) -> None:
    log = ShadowOpportunityLog(tmp_path / "shadow.sqlite3")
    now = datetime(2026, 11, 16, 4, tzinfo=timezone.utc)
    original = ShadowScan("session-2", date(2026, 11, 16), "momentum", now, "snapshot",
                          (ShadowOpportunity("opp-1", "NSE:ABC", "SELL", "MOMENTUM", "event"),))
    assert log.record_scan(original)
    assert not log.record_scan(original)
    changed = ShadowScan("session-2", date(2026, 11, 16), "momentum", now, "snapshot",
                         (ShadowOpportunity("opp-2", "NSE:ABC", "SELL", "MOMENTUM", "event"),))
    with pytest.raises(ShadowLogError, match="immutable"):
        log.record_scan(changed)
    assert not log.session_coverage("session-2")["complete"]
    log.close()


def test_shadow_scan_rejects_duplicate_candidates_and_naive_time() -> None:
    dup = ShadowOpportunity("same", "ABC", "BUY", "A", "x")
    with pytest.raises(ShadowLogError, match="timezone"):
        ShadowScan("s", date(2026, 11, 16), "swing", datetime(2026, 11, 16), "snap", ())
    with pytest.raises(ShadowLogError, match="duplicate"):
        ShadowScan("s", date(2026, 11, 16), "swing", datetime(2026, 11, 16, tzinfo=timezone.utc),
                   "snap", (dup, dup))
