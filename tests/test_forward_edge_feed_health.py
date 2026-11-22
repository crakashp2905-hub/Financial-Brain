from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.feed_health import FeedHealthError, FeedHealthMonitor


START = datetime(2026, 11, 13, 4, 45, tzinfo=timezone.utc)


def test_monitor_records_failures_recovery_coverage_repeated_payloads_and_poll_gaps():
    monitor = FeedHealthMonitor(("NSE:INFY", "NSE:TCS"),
                                poll_interval_seconds=60, stale_after_seconds=90)
    quote = {"timestamp": "2026-11-13 10:15:00", "last_price": 100.0}
    monitor.record_attempt(START)
    monitor.record_failure(START, RuntimeError("opaque provider failure"))
    monitor.record_attempt(START + timedelta(seconds=60))
    monitor.record_success(START + timedelta(seconds=60), {"NSE:INFY": quote})
    monitor.record_attempt(START + timedelta(seconds=240))
    monitor.record_success(START + timedelta(seconds=240), {"NSE:INFY": quote})

    report = monitor.snapshot(now=START + timedelta(seconds=241))
    assert report["status"] == "DEGRADED"
    assert report["poll_attempts"] == 3
    assert report["successful_polls"] == 2 and report["failed_polls"] == 1
    assert report["reconnects"] == 1
    assert report["missing_quote_count"] == 2 and report["missing_quote_polls"] == 2
    assert report["unchanged_snapshot_count"] == 1
    assert report["missed_poll_slots"] == 2
    assert report["last_success_at"] == (START + timedelta(seconds=240)).isoformat()
    assert report["dropped_packet_count"] is None


def test_stale_feed_produces_explicit_no_trade_state_and_preserves_failure_count():
    monitor = FeedHealthMonitor(("NSE:INFY",), poll_interval_seconds=30, stale_after_seconds=90)
    monitor.record_attempt(START)
    monitor.record_failure(START, TimeoutError())

    report = monitor.snapshot(now=START + timedelta(seconds=91))
    assert report["status"] == "STALE"
    assert report["decision_status"] == "NO_TRADE"
    assert report["failed_polls"] == report["consecutive_failures"] == 1
    assert report["last_success_at"] is None


def test_no_observations_are_explicit_no_data_and_block_decisions():
    monitor = FeedHealthMonitor(("NSE:INFY",), poll_interval_seconds=30, stale_after_seconds=90)
    report = monitor.snapshot(now=START)
    assert report["status"] == "NO_DATA"
    assert report["decision_status"] == "NO_TRADE"
    assert report["unavailable_reason"] is None


@pytest.mark.parametrize("reason", ["MARKET_CLOSED", "UNAUTHORIZED", "PROVIDER_OUTAGE"])
def test_closed_unauthorized_or_outage_feed_is_no_data_and_blocks_decisions(reason):
    monitor = FeedHealthMonitor(("NSE:INFY",), poll_interval_seconds=30, stale_after_seconds=90)
    monitor.record_attempt(START)
    monitor.record_success(START, {"NSE:INFY": {"last_price": 100}})
    report = monitor.snapshot(now=START, unavailable_reason=reason)
    assert report["status"] == "NO_DATA"
    assert report["decision_status"] == "NO_TRADE"
    assert report["unavailable_reason"] == reason


def test_unknown_unavailability_reason_is_rejected():
    monitor = FeedHealthMonitor(("NSE:INFY",), poll_interval_seconds=30, stale_after_seconds=90)
    with pytest.raises(FeedHealthError, match="unsupported"):
        monitor.snapshot(now=START, unavailable_reason="SOMETHING_ELSE")


def test_rest_polling_does_not_fabricate_packet_loss_counts():
    monitor = FeedHealthMonitor(("NSE:INFY",), poll_interval_seconds=60, stale_after_seconds=90)
    monitor.record_attempt(START)
    monitor.record_success(START, {"NSE:INFY": {"last_price": 100.0}})
    report = monitor.snapshot(now=START)
    assert report["missed_poll_slots"] == 0
    assert report["dropped_packet_count"] is None
    assert "no packet sequence" in report["dropped_packet_count_reason"]
