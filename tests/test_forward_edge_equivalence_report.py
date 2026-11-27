from __future__ import annotations

from financial_brain.forward_edge.fixtures import fixture_pack
from scripts.forward_edge_equivalence_report import build_report


def test_equivalence_report_covers_every_fixed_fixture_and_explains_no_trade() -> None:
    report = build_report()
    assert {row["fixture"] for row in report["fixtures"]} == set(fixture_pack())
    assert report["source_check"]["all_legacy_fixture_contracts_match"] is True
    assert report["source_check"]["all_integrated_fixture_contracts_match"] is True
    assert all(len(row["input_contract_sha256"]) == 64 for row in report["fixtures"])
    assert all(row["same_ordered_input_replay_matches"] for row in report["fixtures"])
    assert report["source_check"]["all_same_ordered_input_replays_match"] is True
    assert report["source_check"]["all_rejection_codes_match"] is False
    assert all(any("taxonomy differs" in text for text in row["divergences_and_explanations"])
               for row in report["fixtures"] if not row["rejection_comparison"]["matched"])
    no_trade = next(row for row in report["fixtures"] if row["fixture"] == "no_trade")
    assert no_trade["legacy_pipeline"]["outcome"] == "NO_TRADE"
    assert no_trade["integrated_paper_journal"]["economic_outcome"] == "NO_TRADE"
    assert no_trade["integrated_paper_journal"]["paper_intent_count"] == 0
    assert any("No-trade parity" in text for text in no_trade["divergences_and_explanations"])


def test_report_explains_incomplete_unknown_order_as_fail_closed() -> None:
    report = build_report()
    unknown = next(row for row in report["fixtures"] if row["fixture"] == "unknown_order")
    assert unknown["integrated_paper_journal"]["report_status"] == "INCOMPLETE"
    assert any("fail-closed" in text for text in unknown["divergences_and_explanations"])
    assert "not compare historical source revisions" in report["scope"]
    assert any("risk-policy parity is not established" in text for text in report["limitations"])
