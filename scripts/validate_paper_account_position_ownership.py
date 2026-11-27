"""Validate the scoped owner map for paper-account positions and execution journals."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any


CONTRACT = Path("docs/edge-program/paper-account-position-ownership.v1.json")
REQUIRED_SYSTEMS = {
    "broker_oms", "legacy_intraday_paper", "legacy_swing_paper",
    "decision_candidate_paper_book", "mutual_fund_paper_books", "forward_session_paper",
    "options_paper_subledger", "kernel_simulated_oms", "canonical_paper_portfolio",
}


def _owner_exists(root: Path, owner: str) -> bool:
    path, separator, symbol = owner.partition("::")
    source = root / path
    if not source.is_file():
        return False
    if not separator:
        return True
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError):
        return False
    return any(isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name == symbol for node in ast.walk(tree))


def validate_contract(contract: dict[str, Any], root: Path) -> list[str]:
    errors: list[str] = []
    policy = contract.get("policy", {})
    if policy.get("consolidated_paper_positions_owner") != (
            "src/financial_brain/portfolio/canonical_ledger.py::CanonicalPortfolioLedger"):
        errors.append("the canonical paper position owner must remain CanonicalPortfolioLedger")
    for flag in ("execution_journals_are_not_consolidated_positions",
                 "source_events_are_imported_once_by_source_system_and_event_id",
                 "missing_or_conflicting_fills_remain_unresolved",
                 "no_synthetic_fill_or_exit_is_permitted",
                 "physical_orders_are_outside_paper_position_authority"):
        if policy.get(flag) is not True:
            errors.append(f"required ownership boundary is not asserted: {flag}")
    systems = contract.get("systems")
    if not isinstance(systems, list):
        return [*errors, "systems must be a list"]
    ids = [item.get("id") for item in systems if isinstance(item, dict)]
    if len(ids) != len(set(ids)):
        errors.append("system IDs must be unique")
    if set(ids) != REQUIRED_SYSTEMS:
        errors.append(f"system inventory mismatch: expected {sorted(REQUIRED_SYSTEMS)}, got {sorted(map(str, ids))}")
    canonical_owners = [item for item in systems if isinstance(item, dict)
                        and item.get("role") == "CONSOLIDATED_PAPER_ACCOUNTING"]
    if len(canonical_owners) != 1 or canonical_owners[0].get("id") != "canonical_paper_portfolio":
        errors.append("exactly one system must own consolidated paper accounting")
    for item in systems:
        if not isinstance(item, dict):
            errors.append("each system entry must be an object")
            continue
        owner = item.get("owner")
        if not isinstance(owner, str) or not _owner_exists(root, owner):
            errors.append(f"owner source is missing or unresolved for {item.get('id')}: {owner}")
        for source in item.get("supporting", []):
            if not (root / source).is_file():
                errors.append(f"supporting source is missing for {item.get('id')}: {source}")
        for field in ("authoritative_for", "not_authoritative_for"):
            if not item.get(field):
                errors.append(f"{item.get('id')} must declare {field}")
    if not contract.get("unresolved_domains"):
        errors.append("unresolved domains must remain explicit")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    path = root / CONTRACT
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read paper-account ownership contract: {exc}")
        return 1
    errors = validate_contract(contract, root)
    if errors:
        print("Paper-account ownership contract invalid:")
        print("\n".join(errors))
        return 1
    print("Paper-account ownership contract validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
