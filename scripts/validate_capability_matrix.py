"""Validate conservative, filesystem-backed capability claims in CI.

This does not prove test quality, prospective evidence, or release readiness.
Higher evidence tiers are deliberately not valid matrix states yet.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


MATRIX = Path("docs/audits/capability-matrix.v1.json")
ROLES = {"Research", "Evidence", "Paper", "Execution", "Integration", "UI"}
MATURITY = {"partial", "local_tested"}


def validate(path: Path, root: Path) -> list[str]:
    errors: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"cannot read matrix: {exc}"]
    if not isinstance(payload, dict) or set(payload) != {"schema", "scope", "capabilities"}:
        return ["matrix has missing or unexpected top-level fields"]
    if payload["schema"] != "fb.capability-matrix.v1" or not isinstance(payload["scope"], str):
        errors.append("invalid schema or scope")
    entries = payload["capabilities"]
    if not isinstance(entries, list) or not entries:
        return errors + ["capabilities must be a nonempty list"]
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        label = f"capability {index}"
        if not isinstance(entry, dict) or set(entry) != {
                "id", "owner_role", "maturity", "source", "tests", "blockers"}:
            errors.append(f"{label}: missing or unexpected fields")
            continue
        identifier = entry["id"]
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            errors.append(f"{label}: empty or duplicate id")
        else:
            seen.add(identifier)
            label = identifier
        if not isinstance(entry["owner_role"], str) or entry["owner_role"] not in ROLES:
            errors.append(f"{label}: invalid owner role")
        if not isinstance(entry["maturity"], str) or entry["maturity"] not in MATURITY:
            errors.append(f"{label}: unsupported maturity; no forward or production claim allowed")
        if not isinstance(entry["blockers"], list) or not entry["blockers"] or not all(
                isinstance(x, str) and x.strip() for x in entry["blockers"]):
            errors.append(f"{label}: blockers must be nonempty strings")
        for field, prefix in (("source", "src/financial_brain/"), ("tests", "tests/")):
            paths = entry[field]
            if not isinstance(paths, list) or not paths or len(set(map(str, paths))) != len(paths):
                errors.append(f"{label}: {field} must contain unique paths")
                continue
            for relative in paths:
                if not isinstance(relative, str) or not relative.startswith(prefix) or "\\" in relative:
                    errors.append(f"{label}: invalid {field} path {relative!r}")
                    continue
                resolved = (root / relative).resolve()
                if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
                    errors.append(f"{label}: missing {field} path {relative}")
                if field == "tests" and not Path(relative).name.startswith("test_"):
                    errors.append(f"{label}: test path is not a test module {relative}")
    return errors


if __name__ == "__main__":
    problems = validate(MATRIX, Path("."))
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        raise SystemExit(1)
    print("Capability matrix paths and conservative statuses verified")
