from __future__ import annotations

import json
from pathlib import Path

from scripts.release_candidate_audit import audit


def _candidate(root: Path, *, code: str = "VALUE = 1\n", junit: str | None = None) -> tuple[Path, Path]:
    for directory in ("src", "scripts", ".github"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "src/app.py").write_text(code, encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    rights = root / "docs/edge-program"
    rights.mkdir(parents=True)
    (rights / "data-permissions.v1.json").write_text(json.dumps({"permissions": [
        {"status": "UNKNOWN_RIGHTS"}, {"status": "USE_ALLOWED"},
    ]}), encoding="utf-8")
    readiness = root / "profit_readiness.json"
    readiness.write_text(json.dumps({"gates": [{"id": "profit", "status": "RED",
                                                 "summary": "not demonstrated"}]}), encoding="utf-8")
    junit_path = root / "pytest-results.xml"
    if junit is not None:
        junit_path.write_text(junit, encoding="utf-8")
    return junit_path, readiness


def test_clean_candidate_reports_test_counts_and_external_prerequisites(tmp_path: Path) -> None:
    junit, readiness = _candidate(tmp_path, junit='<testsuite tests="7" skipped="1" failures="0" errors="0"/>')
    report = audit(tmp_path, junit=junit, readiness=readiness)
    assert report["engineering_candidate"] == "PASS"
    assert report["test_coverage_evidence"]["tests"] == 7
    assert report["test_coverage_evidence"]["skipped"] == 1
    assert report["release_candidate"] == "BLOCKED_EXTERNAL_OR_PERFORMANCE_PREREQUISITES"
    assert report["external_prerequisites"]["unresolved_market_data_rights_count"] == 1


def test_developer_path_and_failed_test_counts_remain_visible(tmp_path: Path) -> None:
    junit, readiness = _candidate(
        tmp_path, code='DATA = "D:\\\\Finance-Brain\\\\data\\\\db.duckdb"\n',
        junit='<testsuite tests="9" skipped="2" failures="1" errors="0"/>')
    report = audit(tmp_path, junit=junit, readiness=readiness)
    assert report["engineering_candidate"] == "FAIL"
    assert report["developer_path_findings"]
    assert report["test_coverage_evidence"]["tests"] == 9
    assert report["test_coverage_evidence"]["failures"] == 1


def test_missing_test_report_is_not_reported_as_clean_coverage(tmp_path: Path) -> None:
    junit, readiness = _candidate(tmp_path)
    report = audit(tmp_path, junit=junit, readiness=readiness)
    assert report["engineering_candidate"] == "FAIL"
    assert report["test_coverage_evidence"]["status"] == "MISSING"
