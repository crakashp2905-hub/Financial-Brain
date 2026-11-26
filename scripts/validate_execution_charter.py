"""Validate the fail-closed, content-addressed execution charter."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHARTER = ROOT / "docs/edge-program/execution-charter.v1.json"

def validate() -> None:
    body = json.loads(CHARTER.read_text(encoding="utf-8"))
    claimed = body.get("charter_sha256")
    unsigned = dict(body)
    unsigned["charter_sha256"] = None
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if claimed != hashlib.sha256(canonical).hexdigest():
        raise ValueError("execution charter SHA-256 does not match canonical charter content")
    if body.get("schema") != "fb.execution_charter.v1" or body.get("version") != 1:
        raise ValueError("unsupported execution charter schema")
    if body.get("execution_mode") != "PAPER_ONLY" or body.get("physical_orders_enabled") is not False:
        raise ValueError("execution charter must fail closed to paper only")
    if body.get("capital_budget_inr") != 0:
        raise ValueError("unapproved real-capital budget must remain zero")
    if body.get("model_may_self_approve_real_cash") is not False:
        raise ValueError("models must never self-approve real cash")
    if body.get("human_approval", {}).get("status") != "NOT_SIGNED":
        raise ValueError("owner signature must not be fabricated")
    expected = {"EQUITY", "ETF", "OPTION", "MUTUAL_FUND"}
    rows = body.get("permitted_instruments", [])
    if {row.get("asset_class") for row in rows} != expected or len(rows) != len(expected):
        raise ValueError("execution charter must specify all permitted instrument classes")
    options = next(row for row in rows if row["asset_class"] == "OPTION")
    if "DEFINED_RISK_DEBIT_SPREAD" not in options["allowed_actions"] or any("WRITE" in a for a in options["allowed_actions"]):
        raise ValueError("options must remain limited to long or defined-risk actions")
    mutual = next(row for row in rows if row["asset_class"] == "MUTUAL_FUND")
    if "NAV" not in mutual.get("execution_clock", ""):
        raise ValueError("mutual-fund orders must use NAV-based execution")

if __name__ == "__main__":
    validate()
    print("execution charter hash, scope and fail-closed policy are valid")
