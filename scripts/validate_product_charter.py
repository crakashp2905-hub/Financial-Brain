"""Validate the explicit five-desk product and execution scope charter."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHARTER = ROOT / "docs/edge-program/product-charter.v1.json"
EXPECTED = {"SWING", "INTRADAY", "OPTIONS", "ETF", "MUTUAL_FUND"}


def validate() -> None:
    body = json.loads(CHARTER.read_text(encoding="utf-8"))
    if body.get("schema") != "fb.product_charter.v1" or body.get("version") != 1:
        raise ValueError("unsupported product charter schema or version")
    if body.get("execution_policy", {}).get("physical_orders_enabled") is not False:
        raise ValueError("physical order policy must remain disabled")
    desks = body.get("desks")
    if not isinstance(desks, list) or {row.get("desk") for row in desks} != EXPECTED:
        raise ValueError("charter must define each of the five desks exactly once")
    if len(desks) != len(EXPECTED):
        raise ValueError("desk identifiers must be unique")
    by_name = {row["desk"]: row for row in desks}
    if by_name["OPTIONS"].get("prohibited_until_separately_modelled") is None:
        raise ValueError("options scope must explicitly constrain unbounded/unmodelled risk")
    mf = by_name["MUTUAL_FUND"]
    if "NAV" not in mf.get("clock", "") or "never exchange ticks" not in mf.get("execution_mechanics", ""):
        raise ValueError("mutual-fund scope must use NAV/business-day execution, not exchange ticks")
    if any(row.get("implementation_claim") != "SCOPE_ONLY" for row in desks):
        raise ValueError("product charter must not imply that desk capability is implemented")
    if len(body.get("success_criteria", [])) < 3 or len(body.get("shared_invariants", [])) < 4:
        raise ValueError("charter is missing success criteria or shared invariants")
    objective = body.get("business_objective", {})
    if ("expected and realized prospective risk-adjusted net value" not in objective.get("objective", "")
            or objective.get("current_status") != "NOT_DEMONSTRATED"):
        raise ValueError("charter must state the prospective risk-adjusted net objective without claiming success")
    if not {"NO_TRADE", "CASH"}.issubset(objective.get("valid_decisions", [])):
        raise ValueError("business objective must retain NO_TRADE and CASH as valid outcomes")
    if not {"Positive P&L every day", "Maximum signal or trade frequency"}.issubset(
            objective.get("not_objectives", [])):
        raise ValueError("charter must reject daily positive P&L and forced trade frequency as objectives")
    required_costs = {"fees", "spread", "slippage", "market impact", "financing", "taxes"}
    basis = objective.get("evaluation_basis", "").lower()
    if not all(cost in basis for cost in required_costs):
        raise ValueError("objective must include full trading costs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the committed charter")
    parser.parse_args()
    validate()
    print("five-desk product charter is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
