from __future__ import annotations

import json

from scripts.research_outcome_freeze import OUTPUT, build_inventory, render, validate


def test_checked_in_research_results_are_frozen_with_explicit_provenance_gaps():
    inventory = build_inventory()

    assert inventory["result_note_count"] == 42
    assert len(inventory["records"]) == 42
    assert set(inventory["result_notes_missing_required_metadata"]) == {
        "run_id", "cost_assumptions", "train_period", "validation_period", "test_period", "exposure"
    }
    assert all(inventory["result_notes_missing_required_metadata"].values())
    assert validate() == []


def test_inventory_hashes_result_bytes_and_requires_machine_readable_run_fields(tmp_path):
    result_dir = tmp_path / "brain/07-Research"
    result_dir.mkdir(parents=True)
    result_file = result_dir / "Result example.md"
    result_file.write_text(
        "---\nrun_id: R-1\ncost_assumptions: fee-v1\ntrain_period: 2018-2020\n"
        "validation_period: 2021\ntest_period: 2022\nexposure: long-only\n---\n# Result\n",
        encoding="utf-8",
    )

    before = build_inventory(tmp_path)
    assert before["result_note_count"] == 1
    assert not any(before["result_notes_missing_required_metadata"].values())
    first_hash = before["records"][0]["sha256_lf"]

    result_file.write_text(result_file.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
    after = build_inventory(tmp_path)
    assert after["records"][0]["sha256_lf"] != first_hash
    assert render(tmp_path).endswith("\n")


def test_inventory_check_rejects_missing_or_changed_result_artifact(tmp_path):
    (tmp_path / "brain/07-Research").mkdir(parents=True)
    output = tmp_path / OUTPUT
    output.parent.mkdir(parents=True)
    output.write_text(json.dumps(build_inventory(tmp_path), indent=2) + "\n", encoding="utf-8")
    assert validate(tmp_path) == []

    (tmp_path / "brain/07-Research/Result new.md").write_text("# new outcome\n", encoding="utf-8")
    assert any("stale" in error for error in validate(tmp_path))
