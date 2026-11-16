"""Regression oracle recovered from the audited forward-edge fixture pipeline."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from financial_brain.forward_edge import pipeline
from financial_brain.forward_edge.fixtures import EXPECTED, fixture_pack

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = json.loads((ROOT / "tests/fixtures/forward_edge/golden_signatures.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_historical_fixture_runs_through_runner_without_changing_legacy_contract(name: str) -> None:
    result = pipeline.run_session(fixture_pack()[name])
    assert result["outcome"] == EXPECTED[name]
    assert result["signature"] == GOLDEN[name]
    assert result["ledger_balanced"] is True
    assert result["cash_equals_net"] is True
    assert result["physical_orders"] is False
    assert result["broker"] == "NONE"
    assert result["forward_runner"]["physical_orders"] is False
    if EXPECTED[name] == "NO_TRADE":
        assert result["forward_runner"]["report_status"] == "CLOSED"
        assert result["forward_runner"]["decision_count"] == 1
    else:
        assert result["forward_runner"]["report_status"] == "INCOMPLETE"
        assert result["forward_runner"]["paper_intent_count"] == 0


def test_golden_signatures_cover_the_complete_fixture_pack() -> None:
    assert set(GOLDEN) == set(fixture_pack()) == set(EXPECTED)


def test_fixture_result_and_runner_evidence_are_deterministic() -> None:
    fixture = fixture_pack()["win"]
    first = pipeline.run_session(fixture)
    second = pipeline.run_session(fixture)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
