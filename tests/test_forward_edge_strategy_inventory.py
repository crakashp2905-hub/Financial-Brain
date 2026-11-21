from pathlib import Path

from financial_brain.forward_edge.strategy_inventory import (
    build_strategy_inventory,
    render_strategy_inventory,
)


REPO = Path(__file__).resolve().parents[1]


def test_inventory_covers_catalogue_and_keeps_missing_operating_records_unverified():
    inventory = build_strategy_inventory(REPO)
    assert inventory.rows
    assert len(inventory.inventory_sha256) == 64
    assert all(row.frozen_version == "NOT_FROZEN" for row in inventory.rows)
    assert all(row.enrollment == "NOT_ENROLLED" for row in inventory.rows)
    assert all(row.evidence_tier in {"FAILED_HISTORICAL", "REFERENCE_ONLY", "UNVERIFIED"}
               for row in inventory.rows)
    assert any(row.eligibility == "FAILED" for row in inventory.rows)
    assert inventory.rows[0].eligibility == "FAILED"


def test_inventory_uses_explicit_operating_overlays_and_escapes_html(tmp_path):
    inventory = build_strategy_inventory(
        REPO, frozen_versions={"TV-001": "v4"},
        enrollments={"TV-001": "SHADOW"}, evidence_tiers={"trial:example": "PAPER_FORWARD"})
    row = next(item for item in inventory.rows if item.strategy_id == "TV-001")
    assert (row.frozen_version, row.enrollment, row.evidence_tier) == (
        "v4", "SHADOW", "REFERENCE_ONLY")
    altered = build_strategy_inventory(REPO, enrollments={"TV-001": "<script>"})
    path = render_strategy_inventory(altered, tmp_path / "strategies.html")
    page = path.read_text(encoding="utf-8")
    assert "&lt;script&gt;" in page
    assert "<script>" not in page


def test_failed_historical_candidates_are_sorted_before_candidates():
    inventory = build_strategy_inventory(REPO)
    first_nonfailed = next(index for index, row in enumerate(inventory.rows)
                           if row.eligibility != "FAILED")
    assert all(row.eligibility == "FAILED" for row in inventory.rows[:first_nonfailed])
    assert inventory.rows[first_nonfailed].eligibility != "FAILED"
