from __future__ import annotations

import json

from scripts.validate_reference_candidates import MANIFEST, validate


def test_historical_reference_candidates_are_fingerprinted_without_edge_promotion() -> None:
    assert validate() == []
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert {row["id"] for row in manifest["candidates"]} == {"H005", "H003", "P3"}
    assert all(row["prospective_status"] == "NOT_CONFIRMED" for row in manifest["candidates"])
    p3 = next(row for row in manifest["candidates"] if row["id"] == "P3")
    assert "failed historical verdict" in p3["historical_status_in_source"]


def test_validator_fails_if_source_pin_or_candidate_claim_is_changed(tmp_path, monkeypatch) -> None:
    original = MANIFEST.read_text(encoding="utf-8")
    altered = json.loads(original)
    altered["claim_policy"]["prospective_edge_confirmed"] = True
    monkeypatch.setattr("scripts.validate_reference_candidates.MANIFEST", tmp_path / "manifest.json")
    (tmp_path / "manifest.json").write_text(json.dumps(altered), encoding="utf-8")
    assert any("confirmed prospective" in error for error in validate())
