from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.forward_edge.execution_shortfall import (
    ExecutionShortfallError,
    ExecutionShortfallInput,
    report_execution_shortfall,
)


T0 = datetime(2026, 11, 26, 4, tzinfo=timezone.utc)


def _input(**overrides):
    values = dict(
        side="BUY", order_quantity=10, filled_quantity=7,
        decision_bid=D("99"), decision_ask=D("101"),
        arrival_bid=D("100"), arrival_ask=D("102"),
        observed_paper_fill=D("102.5"), model_expected_fill=D("102.25"),
        decision_quote_status="OBSERVED", arrival_quote_status="OBSERVED",
        paper_fill_model_id="paper-fill-v3",
        decision_at=T0, arrival_at=T0 + timedelta(seconds=2),
        fill_at=T0 + timedelta(seconds=3),
    )
    values.update(overrides)
    return ExecutionShortfallInput(**values)


def test_shortfall_bridge_reconciles_and_labels_the_fill_model_hypothetical():
    report = report_execution_shortfall(_input())
    bp = report["shortfall_bp_of_decision_mid"]
    assert bp["decision_mid_to_fill"] == pytest.approx(
        bp["decision_mid_to_touch"] + bp["decision_touch_to_arrival_touch"]
        + bp["arrival_touch_to_observed_fill"])
    assert report["bridge_reconciles"] is True
    assert report["reference_evidence"]["paper_fill_model"] == "HYPOTHETICAL_MODEL_REFERENCE"
    assert report["hypothetical_references"] == ["PAPER_FILL_MODEL"]
    assert report["filled_quantity"] == 7


def test_sell_sign_and_unknown_quote_provenance_are_disclosed():
    report = report_execution_shortfall(_input(
        side="SELL", decision_quote_status="UNKNOWN", arrival_quote_status="HYPOTHETICAL"))
    assert report["shortfall_bp_of_decision_mid"]["decision_mid_to_touch"] > 0
    assert report["unknown_references"] == ["DECISION_QUOTE"]
    assert report["hypothetical_references"] == ["ARRIVAL_QUOTE", "PAPER_FILL_MODEL"]


def test_invalid_fill_quantity_or_timestamp_order_is_refused():
    with pytest.raises(ExecutionShortfallError, match="filled quantity"):
        report_execution_shortfall(_input(filled_quantity=11))
    with pytest.raises(ExecutionShortfallError, match="causally ordered"):
        report_execution_shortfall(_input(arrival_at=T0 - timedelta(seconds=1)))
