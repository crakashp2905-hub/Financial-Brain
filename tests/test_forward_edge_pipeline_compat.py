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
def test_historical_fixture_outcome_and_ledger_contract(name: str) -> None:
    result = pipeline.run_session(fixture_pack()[name])
    assert result["outcome"] == EXPECTED[name]
    assert result["ledger_balanced"] is True
    assert result["cash_equals_net"] is True
    assert result["physical_orders"] is False
    assert result["broker"] == "NONE"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_historical_fixture_signature_is_byte_stable(name: str) -> None:
    assert pipeline.run_session(fixture_pack()[name])["signature"] == GOLDEN[name]


def test_golden_signatures_cover_the_complete_fixture_pack() -> None:
    assert set(GOLDEN) == set(fixture_pack()) == set(EXPECTED)
