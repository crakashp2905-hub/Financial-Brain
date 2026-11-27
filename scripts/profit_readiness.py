"""Generate and validate a source-linked paper-trading readiness report.

All counts and gate states are derived from the canonical v3 checklist ledger. The
generated report deliberately contains no manually editable completion percentage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from financial_brain.forward_edge.forward_evidence_gate import evaluate_forward_evidence_gate

SCHEMA = "fb.profit_readiness.v1"
STATUS_PATH = Path("docs/edge-program/v3.0-status.json")
OUTPUT_PATH = Path("docs/edge-program/profit-readiness.json")
GATE_PREFIXES: dict[str, tuple[str, ...]] = {
    "engineering_integrity": ("PA-00-", "PA-01-"),
    "market_data_and_rights": ("PA-02-", "PA-03-"),
    "paper_execution_and_accounting": ("PA-04-", "SL-22-"),
    "cost_and_strategy_economics": ("PA-05-", "PA-06-", "PA-07-", "PA-08-"),
}
SEVERITY = {"GREEN": 0, "AMBER": 1, "RED": 2}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_text_bytes(path: Path) -> bytes:
    """Read repository text identically from LF and CRLF working trees."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def _gate_status(counts: Counter[str], total: int) -> str:
    if total == 0:
        return "RED"
    if counts.get("NOT_STARTED", 0) or counts.get("EXTERNAL_BLOCKED", 0):
        return "RED"
    if counts.get("PARTIAL", 0) or counts.get("UNASSESSED", 0):
        return "AMBER"
    return "GREEN"


def build(root: Path) -> dict[str, Any]:
    root = root.resolve()
    status_path = root / STATUS_PATH
    audit_path = root / "docs/edge-program/v3.0-audit.jsonl"
    status_bytes = _canonical_text_bytes(status_path)
    status = json.loads(status_bytes.decode("utf-8"))
    audit_rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    items = status.get("items")
    if not isinstance(items, list) or len(items) != status.get("total_items"):
        raise ValueError("canonical checklist item inventory is incomplete")
    by_id = {str(item["id"]): item for item in items}
    audit_by_id = {str(row["id"]): row for row in audit_rows}
    if len(by_id) != len(items) or set(by_id) != set(audit_by_id):
        raise ValueError("status ledger and audit export IDs do not match exactly")

    counts = Counter(str(item.get("status")) for item in items)
    expected_counts = {key: counts.get(key, 0) for key in
                       ("COMPLETE", "EXTERNAL_BLOCKED", "NOT_STARTED", "PARTIAL", "UNASSESSED")}
    if status.get("counts_by_status") != expected_counts:
        raise ValueError("canonical checklist counts are stale or hand-edited")
    summary = status.get("assessment_summary", {})
    assessed = len(items) - expected_counts["UNASSESSED"]
    if (summary.get("audited_count") != assessed
            or summary.get("verified_complete_count") != expected_counts["COMPLETE"]
            or summary.get("status_counts") != expected_counts):
        raise ValueError("canonical checklist assessment summary is stale or hand-edited")

    gates: list[dict[str, Any]] = []
    for name, prefixes in GATE_PREFIXES.items():
        selected = [item for item in items if str(item["id"]).startswith(prefixes)]
        gate_counts = Counter(str(item["status"]) for item in selected)
        status_value = _gate_status(gate_counts, len(selected))
        evidence = []
        for item in selected:
            exported = audit_by_id[str(item["id"])]
            evidence.append({
                "item_id": item["id"],
                "status": item["status"],
                "source": exported["source"],
                "commit": exported.get("commit"),
                "blocker": exported.get("blocker"),
                "evidence": exported.get("evidence", []),
                "tests": exported.get("test", []),
            })
        gates.append({"id": name, "status": status_value,
                      "item_count": len(selected), "counts_by_status": {
                          key: gate_counts.get(key, 0) for key in expected_counts
                      }, "evidence": evidence})

    gates.append({
        "id": "physical_order_safety",
        "status": "GREEN" if _physical_orders_disabled(root) else "RED",
        "item_count": 1,
        "counts_by_status": {},
        "evidence": [{"path": "src/financial_brain/execution/order_policy.py",
                       "line": 10,
                       "claim": "Physical order routing remains disabled by the current build policy."}],
    })
    launch_path = root / "docs/edge-program/launch-gates.v1.json"
    launch = json.loads(launch_path.read_text(encoding="utf-8"))
    external = launch.get("current_external_gates")
    if not isinstance(external, dict) or not external:
        raise ValueError("launch contract must declare current external gates")
    external_status = {
        "UNKNOWN_RIGHTS": "RED", "NOT_CONFIGURED": "RED", "NOT_GRANTED": "RED",
        "BLOCKED": "RED", "VERIFIED": "GREEN", "CONFIGURED": "GREEN", "GRANTED": "GREEN",
    }
    unknown = sorted({str(value) for value in external.values()} - set(external_status))
    if unknown:
        raise ValueError(f"external launch gate states need an explicit mapping: {unknown}")
    gates.append({
        "id": "external_launch_prerequisites",
        "status": max((external_status[str(value)] for value in external.values()),
                      key=lambda value: SEVERITY[value]),
        "item_count": len(external), "counts_by_status": {},
        "evidence": [{"path": "docs/edge-program/launch-gates.v1.json", "line": 63,
                       "gate_id": key, "current_state": value,
                       "status": external_status[str(value)]}
                      for key, value in sorted(external.items())],
    })
    evidence_input_path = root / "docs/edge-program/forward-evidence-gate-input.v1.json"
    evidence_gate_path = root / "docs/edge-program/forward-evidence-gate.v1.json"
    evidence_input_bytes = _canonical_text_bytes(evidence_input_path)
    evidence_input = json.loads(evidence_input_bytes.decode("utf-8"))
    if evidence_input.get("schema") != "fb.forward-evidence-gate-input.v1":
        raise ValueError("forward evidence gate input schema is invalid")
    expected_gate = evaluate_forward_evidence_gate(
        plan=evidence_input.get("plan"), sessions=evidence_input.get("sessions"),
        attribution=evidence_input.get("attribution"), benchmark=evidence_input.get("benchmark"),
        unresolved_monetary_invariants=evidence_input.get("unresolved_monetary_invariants"))
    expected_gate["input_sha256"] = _sha256(evidence_input_bytes)
    if (not evidence_gate_path.is_file()
            or json.loads(_canonical_text_bytes(evidence_gate_path).decode("utf-8")) != expected_gate):
        raise ValueError("forward evidence gate report is missing or stale")
    evidence_gate_status = ("RED" if expected_gate["status"] == "RED" else "AMBER")
    gates.append({
        "id": "forward_evidence_gate",
        "status": evidence_gate_status,
        "item_count": 1,
        "counts_by_status": {},
        "evidence": [{"path": "docs/edge-program/forward-evidence-gate.v1.json",
                       "input_sha256": expected_gate["input_sha256"],
                       "report_sha256": expected_gate["report_sha256"],
                       "gate_status": expected_gate["status"],
                       "release_blocked": expected_gate["release_blocked"],
                       "checks": expected_gate["checks"], "issues": expected_gate["issues"]}],
    })
    overall = max((gate["status"] for gate in gates), key=lambda value: SEVERITY[value])
    body: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_by": "scripts/profit_readiness.py",
        "as_of_utc": summary.get("as_of_utc"),
        "checklist": status.get("checklist"),
        "checklist_source_sha256": status.get("source_sha256"),
        "status_ledger_sha256": _sha256(status_bytes),
        "audit_export_sha256": _sha256(_canonical_text_bytes(audit_path)),
        "total_items": len(items),
        "counts_by_status": expected_counts,
        "assessed_count": assessed,
        "unassessed_count": expected_counts["UNASSESSED"],
        "verified_complete_count": expected_counts["COMPLETE"],
        "gate_policy": {
            "green": "all source checklist items in the gate are COMPLETE",
            "amber": "at least one item is PARTIAL or UNASSESSED and none are NOT_STARTED or EXTERNAL_BLOCKED",
            "red": "any item is NOT_STARTED or EXTERNAL_BLOCKED, an external prerequisite is unavailable, the gate has no evidence scope, or physical order safety is violated",
            "percentages": "omitted; all counts and states are derived from the canonical status ledger",
        },
        "overall_status": overall,
        "gates": gates,
        "deployment": {"mode": "PAPER_ONLY", "live_trading_authorized": False,
                       "reason": "A readiness report does not grant broker, compliance, or live-capital authorization."},
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                           allow_nan=False).encode("utf-8")
    body["report_sha256"] = _sha256(canonical)
    return body


def _physical_orders_disabled(root: Path) -> bool:
    policy = root / "src/financial_brain/execution/order_policy.py"
    modes = root / "src/financial_brain/forward_edge/modes.py"
    policy_text = policy.read_text(encoding="utf-8")
    modes_text = modes.read_text(encoding="utf-8")
    return "PHYSICAL_ORDERS_ENABLED = False" in policy_text and (
        "PHYSICAL_ORDERS_ENABLED as PHYSICAL_ORDERS_ENABLED" in modes_text)


def rendered_bytes(root: Path) -> bytes:
    return (json.dumps(build(root), indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def validate(root: Path, output: Path | None = None) -> None:
    target = output or root / OUTPUT_PATH
    if not target.is_file() or target.read_bytes().replace(b"\r\n", b"\n") != rendered_bytes(root):
        raise ValueError(f"profit readiness artifact is stale or hand-edited: {target}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = args.output if args.output.is_absolute() else root / args.output
    expected = rendered_bytes(root)
    if args.check:
        validate(root, target)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(expected)
    report = build(root)
    print(f"profit readiness {'verified' if args.check else 'written'}: {target}; "
          f"overall={report['overall_status']}; "
          f"{report['verified_complete_count']}/{report['total_items']} checklist items COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
