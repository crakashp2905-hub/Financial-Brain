from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.data_use_matrix import DIMENSIONS, build_matrix, rendered_bytes


def test_matrix_covers_each_provider_product_and_each_requested_use() -> None:
    matrix = build_matrix()
    inventory = json.loads((Path(__file__).resolve().parents[1]
                            / matrix["source_inventory"]).read_text(encoding="utf-8"))
    expected = {(source["id"], product) for source in inventory["sources"]
                for product in source["products"]}
    actual = {(row["source_id"], row["product"]) for row in matrix["products"]}
    assert actual == expected
    assert all(tuple(row["use_status"]) == DIMENSIONS for row in matrix["products"])
    assert all(not row["authorization_inferred"] for row in matrix["products"])


def test_unknown_and_unconfigured_are_never_treated_as_allowed() -> None:
    matrix = build_matrix()
    for row in matrix["products"]:
        expected_status = "UNKNOWN_RIGHTS" if row["provider_configured"] else "NOT_CONFIGURED"
        assert set(row["use_status"].values()) == {expected_status}
    assert matrix["synthetic_replay_policy"]["provider_data_included"] is False
    assert matrix["synthetic_replay_policy"]["grants_provider_rights"] is False


def test_matrix_render_is_deterministic_and_rejects_empty_source_products(tmp_path) -> None:
    assert rendered_bytes() == rendered_bytes()
    source = tmp_path / "market-data-sources.v1.json"
    source.write_text(json.dumps({"sources": [{"id": "broken", "provider": "P", "products": []}]}),
                      encoding="utf-8")
    fake_root = tmp_path / "root"
    (fake_root / "docs/edge-program").mkdir(parents=True)
    (fake_root / "docs/edge-program/market-data-sources.v1.json").write_bytes(source.read_bytes())
    with pytest.raises(ValueError, match="no declared products"):
        build_matrix(fake_root)
