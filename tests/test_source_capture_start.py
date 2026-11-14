from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.market_data import FeedHealth, NormalizedEvent
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner, RunnerError
from financial_brain.forward_edge.source_capture_start import (
    CaptureStartError,
    CaptureStartReceipt,
    SourceEntitlement,
    record_capture_start,
)


UTC = timezone.utc
START = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)
DECISION = START + timedelta(minutes=10)


def config():
    return ForwardRunConfig(
        session_id="capture-session", exchange_date="2026-11-16", cohort_id="capture-cohort",
        strategy_sha256="a" * 64, risk_policy_sha256="b" * 64, constraints_sha256="c" * 64,
        ledger_id="capture-ledger", benchmark_id="NIFTY50", instruments=("NSE:INFY",),
        capital_inr="100000", mode=Mode.LIVE_PAPER,
    )


def receipt(**changes):
    values = dict(
        capture_id="kite-capture-20261116-1",
        capture_started_at=START,
        earliest_decision_at=DECISION,
        clock_skew_ms=12.5,
        clock_evidence_id="clock-probe:session-1",
        quote_health=FeedHealth(None, 0, 0, 0),
        entitlements=(SourceEntitlement("kite-quotes", "Kite Connect", True,
                                        "entitlement:owner-1", DECISION + timedelta(hours=1)),),
    )
    values.update(changes)
    return CaptureStartReceipt(**values)


def test_capture_start_is_journaled_before_market_events_with_session_evidence(tmp_path):
    runner = ForwardSessionRunner(tmp_path / "session.jsonl")
    cfg = config()
    marker = record_capture_start(runner, cfg, receipt())
    assert marker["lead_ms"] == 600_000
    assert marker["clock_skew_ms"] == 12.5
    assert marker["source_entitlements"][0]["valid"] is True
    assert marker["entry_allowed_by_entitlement"] is True
    assert marker["quote_health"]["healthy_at_capture_start"] is False
    assert "NO_QUOTES" in marker["quote_health"]["reasons"]
    assert marker == record_capture_start(runner, cfg, receipt())

    event_at = START + timedelta(seconds=1)
    event = NormalizedEvent("QUOTE", "NSE:INFY", event_at, event_at, event_at,
                            (("last_price", "100"),))
    assert runner.append_events(cfg, [event]) == 1
    records = [row for row in DurableJournal(runner.journal_path).records()
               if row.get("session_id") == cfg.session_id]
    start_seq = next(i for i, row in enumerate(records) if row.get("t") == "SOURCE_CAPTURE_START")
    event_seq = next(i for i, row in enumerate(records) if row.get("t") == "MARKET_EVENT")
    assert start_seq < event_seq


def test_capture_after_earliest_decision_clock_is_rejected(tmp_path):
    runner = ForwardSessionRunner(tmp_path / "late.jsonl")
    with pytest.raises(CaptureStartError, match="before the earliest"):
        record_capture_start(runner, config(), receipt(capture_started_at=DECISION))
    assert not (tmp_path / "late.jsonl").exists()


def test_missing_expired_or_disabled_entitlements_are_visible_and_block_eligibility(tmp_path):
    expired = SourceEntitlement("kite-quotes", "Kite Connect", True,
                                "entitlement:owner-1", START - timedelta(seconds=1))
    runner = ForwardSessionRunner(tmp_path / "entitlement.jsonl")
    marker = record_capture_start(runner, config(), receipt(entitlements=(expired,)))
    assert marker["entry_allowed_by_entitlement"] is False
    assert marker["entitlement_blockers"] == ["SOURCE_ENTITLEMENT_MISSING_OR_INVALID"]

    empty_runner = ForwardSessionRunner(tmp_path / "empty.jsonl")
    empty = record_capture_start(empty_runner, config(), receipt(entitlements=()))
    assert empty["entry_allowed_by_entitlement"] is False


def test_receipt_cannot_be_changed_or_added_after_capture_events(tmp_path):
    runner = ForwardSessionRunner(tmp_path / "immutable.jsonl")
    cfg = config()
    first = receipt()
    record_capture_start(runner, cfg, first)
    changed = receipt(clock_skew_ms=14.0)
    with pytest.raises(RunnerError, match="immutable"):
        record_capture_start(runner, cfg, changed)

    other = ForwardSessionRunner(tmp_path / "late-record.jsonl")
    stamp = START + timedelta(seconds=1)
    quote = NormalizedEvent("QUOTE", "NSE:INFY", stamp, stamp, stamp,
                            (("last_price", "100"),))
    other.append_events(config(), [quote])
    with pytest.raises(RunnerError, match="before market events"):
        record_capture_start(other, config(), receipt())


def test_clock_evidence_and_capture_identity_are_mandatory(tmp_path):
    runner = ForwardSessionRunner(tmp_path / "invalid.jsonl")
    with pytest.raises(CaptureStartError, match="clock skew"):
        record_capture_start(runner, config(), receipt(clock_skew_ms=float("nan")))
    with pytest.raises(CaptureStartError, match="capture ID"):
        record_capture_start(runner, config(), receipt(capture_id=" "))

