"""Inventory includes every Python source while preserving unknown status."""
import hashlib
from pathlib import Path

import pytest

from scripts.forward_engine_graph import build as build_graph, rendered_bytes as graph_bytes
from scripts.source_inventory import build, canonical_bytes, rendered_bytes
from scripts.state_ownership import load as load_ownership, validate as validate_ownership


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
    assert all(row["source_sha256"] == hashlib.sha256(
        (ROOT / row["source"]).read_bytes().replace(bytes([13, 10]), bytes([10]))).hexdigest()
        for row in one["sources"])


def test_committed_source_and_runner_graph_snapshots_are_current():
    inventory_path = ROOT / "docs/edge-program/source-inventory.v2.json"
    graph_path = ROOT / "docs/edge-program/forward-engine-dependency-graph.v1.json"
    lf = lambda b: b.replace(bytes([13, 10]), bytes([10]))  # noqa: E731 - checkouts may convert to CRLF
    assert lf(inventory_path.read_bytes()) == rendered_bytes(build(ROOT))
    graph = build_graph(ROOT)
    assert lf(graph_path.read_bytes()) == graph_bytes(graph)
    assert graph["source_count"] == build(ROOT)["source_count"]
    assert graph["entrypoints"]["legacy_intraday_paper"] in graph["closures"]["legacy_intraday_paper"]
    assert graph["entrypoints"]["forward_session_runner"] in graph["closures"]["forward_session_runner"]


def test_state_ownership_is_scoped_and_duplicate_authorities_are_rejected():
    registry = load_ownership(ROOT)
    assert len(registry["unresolved_domains"]) == 2
    assert all(row["status"] == "not_established" for row in registry["unresolved_domains"])
    duplicate = dict(registry)
    duplicate["authorities"] = [*registry["authorities"], dict(registry["authorities"][0])]
    with pytest.raises(ValueError, match="duplicate authoritative owner"):
        validate_ownership(duplicate, ROOT)
