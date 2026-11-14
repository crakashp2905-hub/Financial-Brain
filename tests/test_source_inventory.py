"""Inventory includes every Python source while preserving unknown status."""
import hashlib
import json
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


def test_inventory_captures_old_and_new_engine_dependency_graph_roots():
    report = build(ROOT)
    roots = set(report["engine_graph_roots"])
    assert roots == {
        "src/financial_brain/paper/intraday_runner.py",
        "src/financial_brain/forward_edge/pipeline.py",
        "src/financial_brain/forward_edge/session_runner.py",
        "src/financial_brain/ingest/kite_live.py",
    }
    edges = report["local_import_edges"]
    assert all(edge["from"] != edge["to"] for edge in edges)
    assert any(edge["from"] == "src/financial_brain/paper/intraday_runner.py" for edge in edges)
    assert any(edge["from"] == "src/financial_brain/forward_edge/session_runner.py" for edge in edges)


def test_task_ledger_inventory_counts_and_authority_evidence_hash_are_current():
    inventory_path = ROOT / "docs/edge-program/source-inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    task_status = json.loads((ROOT / "docs/edge-program/task-status.json").read_text(encoding="utf-8"))
    task = next(row for row in task_status["items"] if row["id"] == "PE-0011")
    authority_evidence = ROOT / "docs/edge-program/pe-0011-paper-authority-boundary-evidence.md"
    normalized = authority_evidence.read_bytes().replace(b"\r\n", b"\n")

    assert f"{inventory['source_count']} Python sources" in task["test_result"]
    assert f"{inventory['unassessed_count']} unassessed" in task["test_result"]
    assert f"{inventory['unassigned_role_count']} role-unassigned" in task["test_result"]
    assert task["evidence_sha256"] == hashlib.sha256(normalized).hexdigest()
