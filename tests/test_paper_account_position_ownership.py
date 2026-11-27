from __future__ import annotations

import copy
import json
from pathlib import Path

from scripts.validate_paper_account_position_ownership import validate_contract

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs/edge-program/paper-account-position-ownership.v1.json"


def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_paper_account_position_ownership_contract_maps_existing_systems() -> None:
    assert validate_contract(_contract(), ROOT) == []


def test_contract_rejects_multiple_canonical_owners_and_missing_sources() -> None:
    contract = _contract()
    duplicate = copy.deepcopy(next(row for row in contract["systems"]
                                   if row["id"] == "canonical_paper_portfolio"))
    duplicate["id"] = "second_canonical_owner"
    contract["systems"].append(duplicate)
    contract["systems"][0]["owner"] = "src/financial_brain/missing.py::NoOwner"
    errors = validate_contract(contract, ROOT)
    assert any("exactly one system" in error for error in errors)
    assert any("owner source is missing" in error for error in errors)


def test_contract_rejects_implicit_fill_or_execution_ownership() -> None:
    contract = _contract()
    contract["policy"]["no_synthetic_fill_or_exit_is_permitted"] = False
    contract["systems"].pop()
    errors = validate_contract(contract, ROOT)
    assert any("no_synthetic_fill_or_exit_is_permitted" in error for error in errors)
    assert any("system inventory mismatch" in error for error in errors)
