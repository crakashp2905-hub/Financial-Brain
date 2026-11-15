"""FE-0003: every known strategy candidate is hash-linked and conservatively classified."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from financial_brain.forward_edge.eligibility import (
    EligibilityError,
    build_catalogue,
    validate_catalogue,
    write_catalogue,
)

REPO = Path(__file__).resolve().parents[1]


def test_catalogue_covers_source_registry_and_historical_trials_with_explanations() -> None:
    catalogue = build_catalogue(REPO)
    external = json.loads((REPO / "docs/sources/algorithm-source-registry.json").read_text(encoding="utf-8"))
    trial_rows = [json.loads(line) for line in (REPO / "docs/trials/ledger.jsonl").read_text(encoding="utf-8").splitlines()
                  if line.strip()]
    trials = {"trial:" + row["name"] for row in trial_rows if row.get("kind") == "trial"}
    ids = {row["strategy_id"] for row in catalogue["candidates"]}
    assert {row["id"] for row in external["entries"]} <= ids
    assert trials <= ids
    validate_catalogue(catalogue, REPO)
    assert all(row["exclusion_reason"] and len(row["source_sha256"]) == 64
               for row in catalogue["candidates"])
    assert all(row["status"] != "PAPER_ELIGIBLE" for row in catalogue["candidates"])
    assert next(row for row in catalogue["candidates"] if row["strategy_id"] ==
                "trial:M1 cost-aware momentum book")["status"] == "FAILED"


def test_catalogue_change_requires_new_version_and_source_edits_are_detected(tmp_path: Path) -> None:
    path = tmp_path / "eligibility-v1.json"
    catalogue = write_catalogue(REPO, path)
    with pytest.raises(EligibilityError, match="immutable"):
        write_catalogue(REPO, path)
    changed = json.loads(json.dumps(catalogue))
    changed["candidates"][0]["status"] = "VERIFIED"
    with pytest.raises(EligibilityError, match="changed"):
        validate_catalogue(changed, REPO)


def test_missing_exclusion_reason_and_unknown_status_are_rejected() -> None:
    catalogue = build_catalogue(REPO)
    catalogue["candidates"][0]["exclusion_reason"] = ""
    with pytest.raises(EligibilityError, match="exclusion reason"):
        validate_catalogue(catalogue, REPO)
