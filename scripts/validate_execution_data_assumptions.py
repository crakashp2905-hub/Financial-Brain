"""Validate execution assumptions and source-fixture rights references."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/edge-program/execution-data-assumptions.v1.json"


def validate() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("schema") != "fb.execution_data_assumptions.v1":
        raise ValueError("unsupported execution assumptions schema")
    permissions_path = ROOT / manifest["market_data_rights_register"]
    permissions = json.loads(permissions_path.read_text(encoding="utf-8"))
    ids = {item["source_id"] for item in permissions.get("permissions", [])}
    for row in manifest.get("fixture_sources", []):
        fixture_path = ROOT / row["fixture"]
        if not fixture_path.is_file():
            raise ValueError(f"missing fixture manifest target: {row['fixture']}")
        if row["source_class"] == "SYNTHETIC_FIXTURE":
            if row.get("rights_status") != "NOT_APPLICABLE_SYNTHETIC":
                raise ValueError("synthetic fixture rights status must remain explicit")
        else:
            permission_id = row.get("rights_permission_source_id")
            if permission_id not in ids or not row.get("retention") or not row.get("redistribution"):
                raise ValueError("observed fixture must bind rights ID, retention and redistribution scope")
    paper = manifest["execution_assumptions"]["paper_exchange"]
    broker = manifest["execution_assumptions"]["broker"]
    multi = manifest["execution_assumptions"]["multi_leg"]
    if paper["cancel_request"] != "does_not_cancel_until_cancel_confirmation_event":
        raise ValueError("paper cancel request cannot imply cancellation before confirmation")
    if broker["runtime_policy"] != "PHYSICALLY_DISABLED":
        raise ValueError("broker execution lock must remain explicit")
    if multi["atomic_exchange_combo_order"] != "NOT_ASSUMED" or not multi["legging_modes"]:
        raise ValueError("multi-leg execution must explicitly represent non-atomic legging")


if __name__ == "__main__":
    validate()
    print("execution assumptions and source rights references verified")
