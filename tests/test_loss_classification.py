from __future__ import annotations

import duckdb
import pytest

from financial_brain.paper.loss_classification import (
    LossClassificationError,
    classify_loss,
    store_classification,
)


def test_losing_trade_without_evidence_is_explicitly_unknown():
    result = classify_loss(trade_id="t1", day="2026-11-12", net_pnl=-10, evidence=[])
    assert result["primary_category"] == "UNKNOWN"
    assert result["categories"] == []
    assert result["evidence"] == []


def test_supported_categories_retain_evidence_ids_and_sort_deterministically():
    result = classify_loss(trade_id="t1", day="2026-11-12", net_pnl=-10, evidence=[
        {"category": "STALE_DATA", "evidence_id": "feed:44"},
        {"category": "EXECUTION_COST", "evidence_id": "fill:8"},
        {"category": "STALE_DATA", "evidence_id": "feed:44"},
    ])
    assert result["primary_category"] == "EXECUTION_COST"
    assert result["categories"] == ["EXECUTION_COST", "STALE_DATA"]
    assert result["evidence"] == [
        {"category": "EXECUTION_COST", "evidence_id": "fill:8"},
        {"category": "STALE_DATA", "evidence_id": "feed:44"},
    ]


def test_nonloss_does_not_receive_a_loss_category():
    result = classify_loss(trade_id="t1", day="2026-11-12", net_pnl=0, evidence=None)
    assert result["primary_category"] == "NOT_A_LOSS"
    assert not result["categories"]


def test_unknown_categories_and_missing_evidence_ids_are_rejected():
    with pytest.raises(LossClassificationError, match="unknown loss category"):
        classify_loss(trade_id="t1", day="d", net_pnl=-1,
                      evidence=[{"category": "GUESS", "evidence_id": "x"}])
    with pytest.raises(LossClassificationError, match="evidence_id"):
        classify_loss(trade_id="t1", day="d", net_pnl=-1,
                      evidence=[{"category": "TIMING", "evidence_id": " "}])


def test_classification_storage_is_idempotent_and_rejects_retroactive_change(tmp_path):
    con = duckdb.connect(str(tmp_path / "loss.duckdb"))
    result = classify_loss(trade_id="t1", day="2026-11-12", net_pnl=-10,
                           evidence=[{"category": "TIMING", "evidence_id": "decision:1"}])
    assert store_classification(con, result, recorded_at_utc="2026-11-13T00:00:00Z")
    assert not store_classification(con, result, recorded_at_utc="2026-11-14T00:00:00Z")
    stored = con.execute("SELECT primary_category, evidence_json FROM loss_classifications").fetchone()
    assert stored == ("TIMING", '[{"category":"TIMING","evidence_id":"decision:1"}]')
    changed = classify_loss(trade_id="t1", day="2026-11-12", net_pnl=-10,
                            evidence=[{"category": "TIMING", "evidence_id": "decision:2"}])
    with pytest.raises(LossClassificationError, match="immutable"):
        store_classification(con, changed)


def test_classification_storage_rejects_naive_timestamp(tmp_path):
    con = duckdb.connect(str(tmp_path / "loss.duckdb"))
    result = classify_loss(trade_id="t1", day="d", net_pnl=-1, evidence=None)
    with pytest.raises(LossClassificationError, match="timezone-aware"):
        store_classification(con, result, recorded_at_utc="2026-11-13T00:00:00")
