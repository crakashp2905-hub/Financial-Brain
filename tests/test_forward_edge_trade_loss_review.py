from __future__ import annotations

from datetime import datetime, timezone

import pytest

from financial_brain.forward_edge.trade_loss_review import (
    LossCause,
    TradeLossReview,
    TradeLossReviewError,
    build_trade_loss_review,
)


def _review(**overrides):
    values = dict(
        trade_id="trade-123", net_pnl_inr=-1250.0,
        reviewer_id="analyst-7", reviewed_at=datetime(2026, 11, 27, tzinfo=timezone.utc),
        root_cause=LossCause.EXECUTION, finding="Observed spread widened before the exit; model underestimated cost.",
        source_sha256_by_domain={key: (f"{n:064x}",) for n, key in enumerate(
            ("data", "model", "contract", "execution", "exit", "sizing"), start=1)},
    )
    values.update(overrides)
    return TradeLossReview(**values)


def test_loss_review_cites_all_required_source_domains_and_hashes_the_finding():
    report = build_trade_loss_review(_review())
    assert report["root_cause"] == "EXECUTION"
    assert report["net_pnl_inr"] == -1250
    assert set(report["source_sha256_by_domain"]) == {
        "data", "model", "contract", "execution", "exit", "sizing"}
    assert report["independently_attested"] is False
    assert len(report["review_sha256"]) == 64


def test_loss_review_rejects_uncited_story_invalid_hashes_and_non_losses():
    with pytest.raises(TradeLossReviewError, match="cover all loss domains"):
        build_trade_loss_review(_review(source_sha256_by_domain={"execution": ("a" * 64,)}))
    bad_refs = dict(_review().source_sha256_by_domain)
    bad_refs["data"] = ()
    with pytest.raises(TradeLossReviewError, match="at least one valid"):
        build_trade_loss_review(_review(source_sha256_by_domain=bad_refs))
    with pytest.raises(TradeLossReviewError, match="negative net"):
        build_trade_loss_review(_review(net_pnl_inr=0.0))
