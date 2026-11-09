from __future__ import annotations

import pytest

from financial_brain.kernel.research_registry import (
    HypothesisLedger,
    evidence_label,
    split_report,
)


def test_duplicate_hypothesis_is_refused_and_id_is_stable() -> None:
    ledger = HypothesisLedger()
    first = ledger.register("momentum", "12m lookback")
    assert first.hypothesis_id == HypothesisLedger().register("momentum", "12m lookback").hypothesis_id
    with pytest.raises(ValueError, match="already registered"):
        ledger.register("momentum", "12m lookback")


def test_negative_results_are_preserved_and_outcome_is_not_overwritten() -> None:
    ledger = HypothesisLedger()
    h = ledger.register("pattern", "double bottom 5d")
    ledger.record_outcome(h.hypothesis_id, "FAILED", "no edge after costs")
    assert [x.name for x in ledger.failures()] == ["double bottom 5d"]
    with pytest.raises(ValueError, match="already recorded"):
        ledger.record_outcome(h.hypothesis_id, "PASSED")
    assert "no edge after costs" in ledger.get(h.hypothesis_id).notes


def test_search_finds_past_hypotheses_and_failures() -> None:
    ledger = HypothesisLedger()
    ledger.register("pattern", "cup and handle", "tested on NIFTY")
    ledger.register("momentum", "12m lookback")
    assert [h.name for h in ledger.search("cup")] == ["cup and handle"]
    assert [h.name for h in ledger.search("nifty")] == ["cup and handle"]


def test_evidence_tiers_rank_prospective_above_oos_above_in_sample() -> None:
    assert evidence_label(out_of_sample=False, prospective=False) == "IN_SAMPLE"
    assert evidence_label(out_of_sample=True, prospective=False) == "HISTORICAL_OOS"
    assert evidence_label(out_of_sample=True, prospective=True) == "PROSPECTIVE"


def test_split_report_groups_by_label() -> None:
    report = split_report([0.02, 0.04, -0.01], ["2024", "2024", "2025"])
    assert report["2024"] == pytest.approx(0.03)
    assert report["2025"] == pytest.approx(-0.01)
    with pytest.raises(ValueError):
        split_report([0.1], [])
