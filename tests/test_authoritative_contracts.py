import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "docs/edge-program/authoritative-contracts.v1.json"


def test_contract_inventory_has_unique_owner_and_resolvable_evidence():
    data = json.loads(CONTRACTS.read_text(encoding="utf-8"))
    contracts = data["contracts"]
    assert contracts
    assert len({row["id"] for row in contracts}) == len(contracts)
    for row in contracts:
        assert row["owner"]
        assert row["scope"]
        assert row["boundary"]
        assert row["status"] in {"SCOPED", "PARTIAL"}
        references = [row["owner"], *row["supporting"], *row["alternatives"], *row["evidence"]]
        for reference in references:
            relative = reference.split("::", 1)[0]
            assert (ROOT / relative).is_file(), f"{row['id']}: missing {relative}"


def test_inventory_preserves_unresolved_cross_package_contracts():
    data = json.loads(CONTRACTS.read_text(encoding="utf-8"))
    assert len(data["unresolved"]) >= 3
    assert any("Kite quote adapter" in item for item in data["unresolved"])
    assert any("StrategySpec" in item and "AlgorithmSpec" in item for item in data["unresolved"])
