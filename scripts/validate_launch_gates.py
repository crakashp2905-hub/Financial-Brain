"""Validate fail-closed five-desk launch and evidence-label contracts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/edge-program/launch-gates.v1.json"
CHARTER = ROOT / "docs/edge-program/product-charter.v1.json"
LABELS = {
    "FIXTURE": "DETERMINISTIC_FIXTURE",
    "REPLAY": "HISTORICAL_REPLAY",
    "LOCAL_PAPER": "LOCAL_PAPER",
    "ATTESTED_FORWARD": "INDEPENDENTLY_ATTESTED_FORWARD",
}


def validate(body: dict[str, Any] | None = None) -> None:
    contract = body if body is not None else json.loads(CONTRACT.read_text(encoding="utf-8"))
    charter = json.loads(CHARTER.read_text(encoding="utf-8"))
    if contract.get("schema") != "fb.five_desk_launch_gates.v1" or contract.get("version") != 1:
        raise ValueError("unsupported launch-gate contract schema or version")

    policy = contract.get("promotion_policy", {})
    if policy.get("physical_orders_enabled") is not False or policy.get("live_cash_enabled") is not False:
        raise ValueError("launch-gate contract must keep physical orders and live cash disabled")
    if policy.get("failed_or_unknown_gate_action") != "BLOCK_PROMOTION_AND_REMAIN_PAPER_ONLY":
        raise ValueError("unknown or failed gates must remain fail-closed")
    if policy.get("caller_labels_are_authoritative") is not False:
        raise ValueError("caller-supplied labels cannot grant a higher evidence tier")

    labels = contract.get("evidence_labels")
    if not isinstance(labels, list):
        raise ValueError("evidence labels must be a list")
    by_label = {row.get("label"): row for row in labels if isinstance(row, dict)}
    if len(by_label) != len(labels) or set(by_label) != {*LABELS, "BROKER_APPROVED"}:
        raise ValueError("define each of the five required labels exactly once")
    for label, canonical in LABELS.items():
        row = by_label[label]
        if row.get("kind") != "EVIDENCE_CLASS" or row.get("canonical_class") != canonical:
            raise ValueError(f"{label} must map to its canonical evidence class")
        if not row.get("requirements") or not row.get("claims_forbidden"):
            raise ValueError(f"{label} must define requirements and claim limits")
    if "permitted source and use" not in by_label["LOCAL_PAPER"].get("requirements", []):
        raise ValueError("LOCAL_PAPER requires source/use permission")
    attested = by_label["ATTESTED_FORWARD"].get("requirements", [])
    for requirement in ("sealed pre-entry decision", "complete prospective session", "verified independent custody receipt"):
        if requirement not in attested:
            raise ValueError("ATTESTED_FORWARD requires sealed, complete evidence and verified custody")

    broker = by_label["BROKER_APPROVED"]
    if (broker.get("kind") != "DEPLOYMENT_GATE" or broker.get("canonical_class") is not None
            or broker.get("current_state") != "BLOCKED" or not broker.get("requirements")):
        raise ValueError("BROKER_APPROVED is a separate blocked deployment gate, not self-asserted evidence")
    external = contract.get("current_external_gates", {})
    if external.get("kite_quote_paper_simulation_and_archive_rights") != "UNKNOWN_RIGHTS":
        raise ValueError("do not infer Kite paper/archive permission without reviewed evidence")
    if external.get("broker_approval") != "NOT_GRANTED":
        raise ValueError("broker approval must remain ungranted in this paper-only contract")

    charter_desks = {row["desk"] for row in charter["desks"]}
    desks = contract.get("desks")
    if not isinstance(desks, list) or {row.get("desk") for row in desks} != charter_desks or len(desks) != len(charter_desks):
        raise ValueError("launch gates must cover each product-charter desk exactly once")
    for row in desks:
        if row.get("implementation_claim") != "SCOPE_ONLY" or not row.get("desk_preflight"):
            raise ValueError(f"{row.get('desk')} needs desk-specific gates and a scope-only claim")
        if not row.get("paper_actions") or not row.get("clock") or not row.get("product_scope"):
            raise ValueError(f"{row.get('desk')} is missing product, clock or paper-action scope")
    if not contract.get("global_preflight"):
        raise ValueError("global source, data, risk, cost and reconciliation gates are required")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the committed launch-gate contract")
    parser.parse_args()
    validate()
    print("five-desk launch-gate contract is valid and fail-closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
