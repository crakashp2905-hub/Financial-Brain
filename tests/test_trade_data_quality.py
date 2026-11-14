from __future__ import annotations

import pytest

from financial_brain.forward_edge.trade_data_quality import (
    DataQualityPolicy,
    TradeDataQualityError,
    assess_trade_data_quality,
)


def _policy():
    return DataQualityPolicy("dq-v1", 5.0, 30.0, 0.95,
                             "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z",
                             "2026-02-01T00:00:00Z")


def _trade(trade_id="t1", **changes):
    row = {"trade_id": trade_id, "strategy": "MOMENTUM",
           "decision_at_utc": "2026-01-02T03:00:00Z", "entry_at_utc": "2026-01-02T03:01:00Z",
           "net_pnl_inr": -100.0, "feed_gap_coverage_complete": True,
           "quote_age_seconds": 1.0, "spread_bp": 10.0, "volatility_percentile": 0.5,
           "quote_evidence_id": "quote:1", "volatility_evidence_id": "vol:1"}
    row.update(changes)
    return row


def test_trades_after_feed_gap_are_separated_from_clean_outcomes_with_event_ids():
    report = assess_trade_data_quality(
        trades=[_trade("gap"), _trade("clean", net_pnl_inr=20.0,
                                     decision_at_utc="2026-01-02T03:03:00Z",
                                     entry_at_utc="2026-01-02T03:04:00Z")],
        feed_gaps=[{"event_id": "feed-event-1", "source": "KITE",
                    "started_at_utc": "2026-01-02T02:59:00Z", "ended_at_utc": "2026-01-02T03:00:30Z"}],
        policy=_policy(),
    )
    assert report["groups"]["SYSTEM_ISSUE"]["trades"] == 1
    assert report["groups"]["CLEAN_OBSERVED"]["trades"] == 1
    assert report["trades"][0]["feed_gap_evidence_ids"] == ["feed-event-1"]
    assert "do not by themselves establish strategy failure" in report["interpretation"]


def test_abnormal_spread_high_volatility_and_stale_quote_flags_are_independent():
    report = assess_trade_data_quality(
        trades=[_trade(quote_age_seconds=7.0, spread_bp=40.0, volatility_percentile=0.98)],
        feed_gaps=[], policy=_policy(),
    )
    assert report["trades"][0]["status"] == "SYSTEM_ISSUE"
    assert set(report["trades"][0]["issue_reasons"]) == {"STALE_QUOTE", "ABNORMAL_SPREAD", "HIGH_VOLATILITY"}
    assert report["trades"][0]["evidence_ids_by_issue"]["ABNORMAL_SPREAD"] == ["quote:1"]


def test_missing_market_health_coverage_is_incomplete_not_clean():
    report = assess_trade_data_quality(trades=[_trade("missing", feed_gap_coverage_complete=False,
                                                      spread_bp=None)], feed_gaps=[], policy=_policy())
    assert report["trades"][0]["status"] == "EVIDENCE_INCOMPLETE"
    assert report["groups"]["EVIDENCE_INCOMPLETE"]["trades"] == 1


def test_gap_after_entry_does_not_contaminate_preentry_classification():
    report = assess_trade_data_quality(
        trades=[_trade()], feed_gaps=[{"event_id": "later", "source": "KITE",
                                      "started_at_utc": "2026-01-02T03:02:00Z",
                                      "ended_at_utc": "2026-01-02T03:03:00Z"}], policy=_policy())
    assert report["trades"][0]["status"] == "CLEAN_OBSERVED"


def test_policy_must_be_frozen_before_observation_and_bad_timestamps_rejected():
    with pytest.raises(TradeDataQualityError, match="frozen before"):
        DataQualityPolicy("bad", 5, 30, 0.95, "2026-01-03T00:00:00Z",
                          "2026-01-02T00:00:00Z", "2026-02-01T00:00:00Z")
    with pytest.raises(TradeDataQualityError, match="timezone-aware"):
        DataQualityPolicy("bad", 5, 30, 0.95, "2026-01-01T00:00:00",
                          "2026-01-02T00:00:00Z", "2026-02-01T00:00:00Z")
