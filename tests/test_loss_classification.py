from __future__ import annotations

import duckdb
import pytest

from financial_brain.paper.loss_classification import (
    LossClassificationError,
    LossEvidenceRegistry,
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


def test_registered_artifact_digest_is_required_and_bound_to_loss_classification(tmp_path):
    registry = LossEvidenceRegistry(tmp_path / "loss-evidence.jsonl")
    registry.register(evidence_id="fill:1", category="EXECUTION_COST", artifact_sha256="a" * 64)
    recovered = LossEvidenceRegistry(tmp_path / "loss-evidence.jsonl")
    result = classify_loss(
        trade_id="t2", day="2026-11-12", net_pnl=-5,
        evidence=[{"category": "EXECUTION_COST", "evidence_id": "fill:1"}],
        evidence_registry=recovered,
    )
    assert result["evidence"][0]["artifact_sha256"] == "a" * 64
    con = duckdb.connect(str(tmp_path / "verified-loss.duckdb"))
    assert store_classification(con, result, evidence_registry=recovered)
    with pytest.raises(LossClassificationError, match="verified evidence requires"):
        store_classification(con, result)


def test_unregistered_or_wrong_category_evidence_cannot_be_classified(tmp_path):
    registry = LossEvidenceRegistry(tmp_path / "loss-evidence.jsonl")
    registry.register(evidence_id="feed:1", category="STALE_DATA", artifact_sha256="b" * 64)
    with pytest.raises(LossClassificationError, match="unresolved"):
        classify_loss(
            trade_id="t3", day="2026-11-12", net_pnl=-5,
            evidence=[{"category": "TIMING", "evidence_id": "unknown"}],
            evidence_registry=registry,
        )
    with pytest.raises(LossClassificationError, match="differs"):
        classify_loss(
            trade_id="t3", day="2026-11-12", net_pnl=-5,
            evidence=[{"category": "TIMING", "evidence_id": "feed:1"}],
            evidence_registry=registry,
        )
    with pytest.raises(LossClassificationError, match="immutable"):
        registry.register(evidence_id="feed:1", category="TIMING", artifact_sha256="b" * 64)
