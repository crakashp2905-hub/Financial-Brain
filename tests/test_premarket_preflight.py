from dataclasses import replace
from datetime import date, datetime, timezone
import json

import pytest

from financial_brain.forward_edge.premarket_preflight import (
    PremarketError,
    PremarketPolicy,
    PremarketSnapshot,
    evaluate_premarket,
)


SESSION_DATE = date(2026, 11, 16)
CHECKED_AT = datetime(2026, 11, 16, 3, 30, tzinfo=timezone.utc)
DIGEST = "a" * 64


@pytest.fixture
def inputs():
    return PremarketSnapshot(
        session_id="NSE-2026-11-16",
        session_date=SESSION_DATE,
        checked_at=CHECKED_AT,
        calendar_is_session=True,
        calendar_version="NSE-2026-11-v1",
        calendar_evidence_id="calendar:2026-11-16",
        data_entitlement_active=True,
        data_source="kite-connect-readonly",
        entitlement_evidence_id="entitlement:account-1",
        broker_read_only_session=True,
        broker_session_id="kite-session-123",
        broker_evidence_id="broker:readonly-session-123",
        disk_free_bytes=2_000_000_000,
        disk_probe_evidence_id="disk-probe:abc",
        archive_writable=True,
        archive_probe_evidence_id="archive-probe:def",
        cost_schedule_sha256=DIGEST,
        cost_effective_from=date(2026, 11, 1),
        cost_effective_until=date(2026, 11, 30),
        cost_evidence_id="costs:2026-11",
        strategy_id="momentum",
        strategy_version="v7",
        strategy_spec_sha256=DIGEST,
        strategy_evidence_id="strategy:frozen-v7",
        risk_config_sha256=DIGEST,
        risk_approved=True,
        risk_evidence_id="risk:approved-7",
        clock_skew_ms=21.5,
        clock_evidence_id="clock:probe-7",
    )


@pytest.fixture
def policy():
    return PremarketPolicy(min_disk_free_bytes=1_000_000_000, max_clock_skew_ms=50)


def test_complete_premarket_record_is_ready_and_runner_compatible(inputs, policy):
    report = evaluate_premarket(inputs, policy)
    assert report.approved is True
    assert report.status == "READY"
    result = report.runner_result()
    assert result["approved"] is True
    assert result["reason"] == "PREMARKET_PREFLIGHT_READY"
    assert result["report_sha256"] == report.report_sha256
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    ("field", "value", "reason_code"),
    [
        ("calendar_is_session", None, "market_calendar"),
        ("calendar_evidence_id", None, "market_calendar"),
        ("data_entitlement_active", False, "data_entitlement"),
        ("entitlement_evidence_id", None, "data_entitlement"),
        ("broker_read_only_session", False, "broker_read_only"),
        ("broker_evidence_id", None, "broker_read_only"),
        ("disk_free_bytes", 1, "disk_capacity"),
        ("disk_probe_evidence_id", None, "disk_capacity"),
        ("archive_writable", False, "archive_writable"),
        ("archive_probe_evidence_id", None, "archive_writable"),
        ("cost_schedule_sha256", None, "cost_schedule"),
        ("strategy_version", None, "strategy_version"),
        ("risk_approved", False, "risk_configuration"),
        ("clock_skew_ms", 50.1, "clocks"),
        ("clock_evidence_id", None, "clocks"),
    ],
)
def test_any_missing_mandatory_preflight_item_yields_operator_visible_no_trade(
    inputs, policy, field, value, reason_code
):
    report = evaluate_premarket(replace(inputs, **{field: value}), policy)
    result = report.runner_result()
    assert report.status == "NO_TRADE"
    assert result["approved"] is False
    assert reason_code in result["reason_codes"]
    assert result["reason"].startswith("PREMARKET_NO_TRADE:")
    assert all(isinstance(check["reason"], str) and check["reason"] for check in result["checks"])


def test_cost_table_must_cover_exchange_session_and_hashes_are_valid(inputs, policy):
    stale = replace(inputs, cost_effective_until=date(2026, 11, 15))
    assert "cost_schedule" in evaluate_premarket(stale, policy).reason_codes
    malformed = replace(inputs, cost_schedule_sha256="not-a-hash")
    assert "cost_schedule" in evaluate_premarket(malformed, policy).reason_codes


def test_clock_must_be_aware_and_policy_limits_must_be_valid(inputs):
    with pytest.raises(PremarketError, match="timezone-aware"):
        evaluate_premarket(replace(inputs, checked_at=datetime(2026, 11, 16, 3, 30)),
                           PremarketPolicy(10, 25))
    with pytest.raises(PremarketError, match="non-negative"):
        PremarketPolicy(-1, 25)


def test_operator_reason_lists_all_simultaneous_blockers(inputs, policy):
    report = evaluate_premarket(replace(inputs, archive_writable=False,
                                        risk_approved=False, data_entitlement_active=False), policy)
    assert report.status == "NO_TRADE"
    assert {"archive_writable", "risk_configuration", "data_entitlement"} <= set(report.reason_codes)
