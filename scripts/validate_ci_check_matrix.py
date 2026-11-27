"""Keep the documented CI check matrix aligned with the workflow and prevent duplicate pattern steps."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs/edge-program/ci-check-matrix.v1.json"
WORKFLOW = ROOT / ".github/workflows/ci.yml"


def validate() -> None:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    workflow = WORKFLOW.read_text(encoding="utf-8")
    if matrix.get("schema") != "fb.ci_check_matrix.v1":
        raise ValueError("unsupported CI matrix schema")
    expected: dict[str, str] = {
        "lint": "python -m ruff check src tests",
        "static_types": "python -m mypy",
        "migrations": "python -m pytest -q tests/test_migrations.py",
        "unit": "python -m coverage run --source=financial_brain -m pytest -q -ra --junitxml=pytest-results.xml",
        "integration": "python -m coverage run --source=financial_brain -m pytest -q -ra --junitxml=pytest-results.xml",
        "dependency_audit": "python -m pip_audit --skip-editable",
    }
    categories = {row["category"]: row for row in matrix["checks"]}
    for category, command in expected.items():
        if category not in categories:
            raise ValueError(f"matrix missing {category}")
        if category == "release_provenance":
            for release_command in categories[category].get("commands", []):
                if workflow.count(release_command) != 1:
                    raise ValueError(f"workflow must run release provenance command exactly once: {release_command}")
            continue
        if categories[category].get("command") != command:
            raise ValueError(f"matrix missing or misstates {category}")
        if command not in workflow:
            raise ValueError(f"workflow is missing the {category} command")
        if category in {"unit", "integration"} and categories[category].get("shared_run") != "full_suite":
            raise ValueError(f"{category} must transparently identify the shared full-suite run")
        if category not in {"unit", "integration"} and workflow.count(command) != 1:
            raise ValueError(f"workflow must run {category} command exactly once")
    release_category = "release_provenance"
    if release_category not in categories:
        raise ValueError("matrix missing release_provenance")
    for release_command in categories[release_category].get("commands", []):
        if workflow.count(release_command) != 1:
            raise ValueError(f"workflow must run release provenance command exactly once: {release_command}")
    suite_command = expected["unit"]
    if workflow.count(suite_command) != 1:
        raise ValueError("unit and integration coverage must share exactly one full-suite invocation")
    required_pattern_steps = [
        "Chart-pattern completion matrix is current",
        "Chart-pattern offline demo (network denied)",
        "Chart-pattern package coverage floor",
    ]
    for step in required_pattern_steps:
        if workflow.count(f"- name: {step}") != 1:
            raise ValueError(f"chart-pattern CI step must appear exactly once: {step}")
    if "invariants" not in categories or "coverage" not in categories:
        raise ValueError("matrix must declare invariant and coverage checks")
    for command in categories["invariants"].get("commands", []):
        if command not in workflow:
            raise ValueError(f"workflow is missing declared invariant check: {command}")
    for step in categories["coverage"].get("steps", []):
        if workflow.count(f"- name: {step}") != 1:
            raise ValueError(f"coverage step must appear exactly once: {step}")
    if "on:\n  push:" not in workflow or "  pull_request:" not in workflow:
        raise ValueError("CI must run on push and pull_request")


if __name__ == "__main__":
    validate()
    print("CI check matrix matches workflow; chart-pattern steps are unique")
