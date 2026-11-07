"""Matrix and baseline integrity tests (CP-0001, CP-0010, CP-0336)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "chart_patterns_matrix.py"
COMPLETION = ROOT / "docs" / "audits" / "chart-patterns-completion.json"
BASELINE = ROOT / "docs" / "audits" / "pattern-baseline.json"
IMPL_MAP = ROOT / "docs" / "audits" / "pattern-implementation-map.md"


def _module():
    spec = importlib.util.spec_from_file_location("chart_patterns_matrix", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checklist_parses_exactly_406_unique_rows() -> None:
    rows = _module().parse_checklist(ROOT / "docs" / "audits" / "chart-patterns-master-checklist.md")
    ids = [row["task_id"] for row in rows]
    assert len(ids) == 406
    assert len(set(ids)) == 406
    assert ids[0] == "CP-0001" and ids[-1] == "CP-0406"
    assert sum(1 for row in rows if row["priority"] == "P0") == 310


def test_committed_matrix_covers_every_cp_and_is_current() -> None:
    module = _module()
    rows = module.parse_checklist(ROOT / "docs" / "audits" / "chart-patterns-master-checklist.md")
    committed = json.loads(COMPLETION.read_text(encoding="utf-8"))["rows"]
    assert [row["task_id"] for row in committed] == [row["task_id"] for row in rows]
    assert {row["status"] for row in committed} <= {
        "implemented",
        "verified_existing",
        "not_applicable_with_evidence",
        "incomplete",
    }
    assert module.build_completion(rows) == committed


def test_every_done_row_points_at_existing_files() -> None:
    module = _module()
    assert module.verify_evidence_paths() == []
    committed = json.loads(COMPLETION.read_text(encoding="utf-8"))["rows"]
    for row in committed:
        if row["status"] in ("implemented", "verified_existing"):
            assert row["source_files"] and row["test_files"]
            for rel in row["test_files"]:
                assert (ROOT / rel).exists(), rel


def test_baseline_is_well_formed() -> None:
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert len(data["commit"]) == 40
    assert set(data["dependency_sha256"]) == {"pyproject.toml", "requirements-lock.txt"}


def test_implementation_map_references_real_files() -> None:
    text = IMPL_MAP.read_text(encoding="utf-8")
    for rel in ("src/financial_brain/patterns/spec.py", "src/financial_brain/patterns/pivots.py",
                "src/financial_brain/patterns/catalogue.py", "src/financial_brain/patterns/offline.py"):
        assert rel in text
        assert (ROOT / rel).exists()
