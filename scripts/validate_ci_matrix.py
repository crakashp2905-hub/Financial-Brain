"""Check the declared CI quality matrix against the workflow and detect duplicate chart gates."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs/edge-program/ci-validation-matrix.v1.json"
WORKFLOW = ROOT / ".github/workflows/ci.yml"
REQUIRED = {"ruff", "mypy", "migrations", "invariants", "unit", "integration", "coverage", "dependency_audit"}


def validate(matrix: dict, workflow: str) -> None:
    if matrix.get("schema") != "fb.ci_validation_matrix.v1":
        raise ValueError("unsupported CI matrix schema")
    categories = matrix.get("categories")
    if not isinstance(categories, dict) or set(categories) != REQUIRED:
        raise ValueError("CI matrix must declare ruff, mypy, migrations, invariants, unit, integration, coverage and dependency audit")
    for name, gate in categories.items():
        commands = gate.get("commands", [gate.get("command")])
        if not isinstance(commands, list) or not commands or any(not command for command in commands):
            raise ValueError(f"{name} must name workflow commands")
        for command in commands:
            if command not in workflow:
                raise ValueError(f"{name} command is missing from CI workflow: {command}")
    if categories["unit"].get("command") != categories["integration"].get("command"):
        raise ValueError("unit and integration classifications must reflect the shared full-suite invocation")
    if (categories["unit"].get("status") != "COMBINED_WITH_INTEGRATION"
            or categories["integration"].get("status") != "COMBINED_WITH_UNIT"):
        raise ValueError("combined unit/integration test gate must be disclosed")
    pattern_steps = matrix.get("chart_pattern_steps", {})
    for key in ("completion_matrix_step", "offline_demo_step"):
        label = pattern_steps.get(key)
        expected = pattern_steps.get("expected_occurrences_each")
        if not label or type(expected) is not int or workflow.count(f"- name: {label}") != expected:
            raise ValueError(f"chart-pattern workflow step {label!r} must occur exactly once")
    if not matrix.get("limitations"):
        raise ValueError("CI matrix must state its limitations")


def main() -> int:
    try:
        matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
        workflow = WORKFLOW.read_text(encoding="utf-8")
        validate(matrix, workflow)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"FAIL CI validation matrix: {exc}")
        return 1
    print("CI validation matrix matches workflow; chart-pattern steps are not duplicated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
