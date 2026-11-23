from __future__ import annotations

import json

from scripts.validate_product_charter import CHARTER, validate


def test_product_charter_defines_five_desk_execution_scope_without_claiming_implementation() -> None:
    validate()
    body = json.loads(CHARTER.read_text(encoding="utf-8"))
    assert body["execution_policy"]["physical_orders_enabled"] is False
    desks = {row["desk"]: row for row in body["desks"]}
    assert len(desks) == 5
    assert "DEFINED_RISK_DEBIT_SPREAD" in desks["OPTIONS"]["permitted_paper_actions"]
    assert "NAKED_OPTION_WRITING" in desks["OPTIONS"]["prohibited_until_separately_modelled"]
    assert "never exchange ticks" in desks["MUTUAL_FUND"]["execution_mechanics"]
    assert all(row["implementation_claim"] == "SCOPE_ONLY" for row in desks.values())
    objective = body["business_objective"]
    assert objective["current_status"] == "NOT_DEMONSTRATED"
    assert {"NO_TRADE", "CASH"}.issubset(objective["valid_decisions"])
    assert "Positive P&L every day" in objective["not_objectives"]
    assert "Maximum signal or trade frequency" in objective["not_objectives"]
