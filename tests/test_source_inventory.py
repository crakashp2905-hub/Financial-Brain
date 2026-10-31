"""Inventory includes every Python source while preserving unknown status."""
from pathlib import Path

from scripts.source_inventory import build, canonical_bytes


ROOT = Path(__file__).resolve().parents[1]


def test_inventory_covers_all_python_sources_without_inventing_owners():
    report = build(ROOT)
    actual = {p.relative_to(ROOT).as_posix() for p in (ROOT / "src/financial_brain").rglob("*.py")}
    rows = report["sources"]
    assert {row["source"] for row in rows} == actual
    assert report["source_count"] == len(actual)
    assert all(row["human_owner"] is None for row in rows)
    assert all(row["parse_status"] == "ok" for row in rows)
    assert report["unassessed_count"] > 0


def test_curated_source_has_exact_existing_tests_and_stable_serialization():
    one = build(ROOT)
    two = build(ROOT)
    assert canonical_bytes(one) == canonical_bytes(two)
    manifest = next(row for row in one["sources"] if row["source"].endswith("/decision_manifest.py"))
    assert manifest["maturity"] == "locally_mapped"
    assert "tests/test_decision_manifest.py" in manifest["test_paths"]
    assert all((ROOT / test).is_file() for row in one["sources"] for test in row["test_paths"])
