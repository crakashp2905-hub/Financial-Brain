from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from scripts.validate_launch_gates import CONTRACT, validate


def test_five_desk_launch_gate_contract_is_complete_and_fail_closed() -> None:
    validate()
    body = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert body["promotion_policy"]["physical_orders_enabled"] is False
    assert body["promotion_policy"]["live_cash_enabled"] is False
    assert {row["desk"] for row in body["desks"]} == {
        "SWING", "INTRADAY", "OPTIONS", "ETF", "MUTUAL_FUND"
    }
    broker = next(row for row in body["evidence_labels"] if row["label"] == "BROKER_APPROVED")
    assert broker["kind"] == "DEPLOYMENT_GATE"
    assert broker["current_state"] == "BLOCKED"
    assert broker["canonical_class"] is None


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda body: body["promotion_policy"].update(physical_orders_enabled=True), "physical orders"),
        (lambda body: body["promotion_policy"].update(failed_or_unknown_gate_action="ALLOW"), "fail-closed"),
        (lambda body: next(row for row in body["evidence_labels"] if row["label"] == "LOCAL_PAPER")["requirements"].remove("permitted source and use"), "source/use permission"),
        (lambda body: next(row for row in body["evidence_labels"] if row["label"] == "BROKER_APPROVED").update(current_state="APPROVED"), "separate blocked deployment gate"),
        (lambda body: next(row for row in body["desks"] if row["desk"] == "OPTIONS").update(desk_preflight=[]), "desk-specific gates"),
    ],
)
def test_contract_rejects_unsafe_or_incomplete_gate_changes(
    mutation: Callable[[dict[str, Any]], None], message: str,
) -> None:
    body = json.loads(CONTRACT.read_text(encoding="utf-8"))
    mutation(body)
    with pytest.raises(ValueError, match=message):
        validate(body)
