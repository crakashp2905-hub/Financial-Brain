from __future__ import annotations

import pytest

from financial_brain.kernel.batch20f_ops import (
    archive_layout,
    compare_signals,
    correlated_entry,
    demo_limits,
    eod_summary,
    health,
    journal_intact,
    lag_ms,
    memory_growth_ok,
    plan_migrations,
    retention_action,
    rollback_target,
    rotation_due,
    runbook_steps,
    startup_order,
    watchdog,
)
from financial_brain.kernel.batch20f_safety import (
    may_send_live,
    negative_equity_protection,
    parse_bar_row,
    scan_secrets,
)


def test_migrations_go_up_in_order_and_down_in_reverse() -> None:
    assert plan_migrations(1, 3, [1, 2, 3]) == [2, 3]
    assert plan_migrations(3, 1, [1, 2, 3]) == [3, 2]
    with pytest.raises(ValueError, match="missing"):
        plan_migrations(1, 3, [1, 3])


def test_watchdog_restarts_a_stale_feed() -> None:
    assert watchdog(100.0, 110.0, timeout_s=30.0) == "OK"
    assert watchdog(100.0, 200.0, timeout_s=30.0) == "RESTART"


def test_health_is_healthy_only_when_every_component_is() -> None:
    assert health({"oms": True, "risk": True}) == ("HEALTHY", [])
    assert health({"oms": True, "risk": False, "ui": False}) == ("DEGRADED", ["risk", "ui"])


def test_log_entries_need_a_correlation_id() -> None:
    assert correlated_entry("c1", "ok")["correlation_id"] == "c1"
    with pytest.raises(ValueError):
        correlated_entry("", "ok")


def test_event_lag_refuses_future_timestamps() -> None:
    assert lag_ms(1_000, 1_250) == 250
    with pytest.raises(ValueError):
        lag_ms(2_000, 1_000)


def test_raw_is_archived_and_only_derived_is_deletable() -> None:
    assert retention_action("RAW", age_days=400, raw_days=365, derived_days=90) == "ARCHIVE"
    assert retention_action("DERIVED", age_days=100, raw_days=365, derived_days=90) == "DELETE_CANDIDATE"
    assert retention_action("DERIVED", age_days=10, raw_days=365, derived_days=90) == "KEEP"


def test_rollback_skips_versions_marked_bad() -> None:
    assert rollback_target(["v1", "v2", "v3"], "v3", bad={"v2"}) == "v1"
    assert rollback_target(["v1"], "v1", bad=set()) is None


def test_startup_is_reversed_for_shutdown_and_names_are_unique() -> None:
    up, down = startup_order(["db", "feed", "oms"])
    assert up == ["db", "feed", "oms"] and down == ["oms", "feed", "db"]
    with pytest.raises(ValueError):
        startup_order(["db", "db"])


def test_offsite_copy_cannot_share_the_local_path() -> None:
    archive_layout("D:/archive", "E:/offsite")
    with pytest.raises(ValueError):
        archive_layout("D:/archive", "D:/archive")


def test_secret_rotation_is_due_at_the_maximum_age() -> None:
    assert rotation_due(created_day=0, today=90, max_days=90) is True
    assert rotation_due(created_day=0, today=30, max_days=90) is False


def test_demo_mode_caps_bars_on_small_machines() -> None:
    assert demo_limits(8.0) == {"max_bars": 50_000}
    assert demo_limits(32.0) == {"max_bars": 500_000}


def test_nightly_regression_lists_changed_signals() -> None:
    assert compare_signals({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4}) == ["b", "c"]


def test_eod_summary_counts_and_refuses_negative_counts() -> None:
    assert eod_summary(2, 1, 0) == {"incidents": 2, "freezes": 1, "manual_overrides": 0}
    with pytest.raises(ValueError):
        eod_summary(-1, 0, 0)


def test_long_replay_memory_growth_is_bounded() -> None:
    assert memory_growth_ok([100.0, 105.0, 110.0], max_growth_mb=20.0) is True
    assert memory_growth_ok([100.0, 150.0], max_growth_mb=20.0) is False


def test_known_alerts_have_runbooks_and_unknown_escalates() -> None:
    assert runbook_steps("FEED_STALE")[0] == "check feed connection"
    assert "escalate" in runbook_steps("UNHEARD_OF")[0]


def test_journal_corruption_is_detected_at_the_first_bad_line() -> None:
    import hashlib

    good = "tick:1|" + hashlib.sha256(b"tick:1").hexdigest()[:16]
    bad = "tick:2|0000000000000000"
    assert journal_intact([good]) is None
    assert journal_intact([good, bad]) == 1


def test_malformed_bar_rows_are_refused() -> None:
    assert parse_bar_row("10,12,9,11,100")["high"] == 12.0
    for bad in ("10,12,9,11", "10,x,9,11,100", "10,8,9,11,100", "10,12,9,11,-1"):
        with pytest.raises(ValueError):
            parse_bar_row(bad)


def test_key_like_strings_are_found_by_the_scanner() -> None:
    assert scan_secrets("key = AKIA1234567890ABCDEF") == ["AWS_ACCESS_KEY"]
    assert scan_secrets("api_key = 'abcdefgh1234'") == ["GENERIC_API_KEY"]
    assert scan_secrets("nothing to see") == []


def test_no_actor_can_send_to_a_live_broker() -> None:
    for actor in ("agent", "script", "human", "strategy"):
        assert may_send_live(actor) is False


def test_negative_equity_is_floored_and_flagged() -> None:
    assert negative_equity_protection(-250.0) == {"equity": 0.0, "flag": "LIABILITY", "liability": 250.0}
    assert negative_equity_protection(100.0) == {"equity": 100.0, "flag": None, "liability": None}
