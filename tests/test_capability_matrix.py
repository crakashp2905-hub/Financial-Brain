"""The curated matrix cannot silently cite missing files or claim forward proof."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from scripts.validate_capability_matrix import MATRIX, validate


ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_capabilities_are_conservative_and_have_files():
    assert validate(ROOT / MATRIX, ROOT) == []


def test_missing_source_and_unsupported_promotion_fail(tmp_path):
    payload = json.loads((ROOT / MATRIX).read_text(encoding="utf-8"))
    changed = deepcopy(payload)
    changed["capabilities"][0]["source"] = ["src/financial_brain/does_not_exist.py"]
    changed["capabilities"][0]["maturity"] = "forward_verified"
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    errors = validate(path, ROOT)
    assert any("missing source path" in error for error in errors)
    assert any("unsupported maturity" in error for error in errors)


def test_duplicate_id_and_path_traversal_fail(tmp_path):
    payload = json.loads((ROOT / MATRIX).read_text(encoding="utf-8"))
    payload["capabilities"][1]["id"] = payload["capabilities"][0]["id"]
    payload["capabilities"][1]["tests"] = ["tests/../../outside.py"]
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    errors = validate(path, ROOT)
    assert any("duplicate id" in error for error in errors)
    assert any("missing tests path" in error for error in errors)
