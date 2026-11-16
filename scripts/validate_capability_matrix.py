"""Validate conservative, filesystem-backed capability claims in CI.

This does not prove test quality, prospective evidence, or release readiness.
Higher evidence tiers are deliberately not valid matrix states yet.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


MATRIX = Path("docs/audits/capability-matrix.v2.json")
ROLES = {"Research", "Evidence", "Paper", "Execution", "Integration", "UI"}
MATURITY = {"partial", "local_tested"}
MODES = {"REAL_FEED", "RESEARCH_REPLAY", "SHADOW", "LIVE_PAPER", "BROKER_DRYRUN", "LIVE_CASH"}


def validate(path: Path, root: Path) -> list[str]:
    errors: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"cannot read matrix: {exc}"]
    if not isinstance(payload, dict) or set(payload) != {
            "schema", "scope", "required_modes", "capabilities"}:
        return ["matrix has missing or unexpected top-level fields"]
    if payload["schema"] != "fb.capability-matrix.v2" or not isinstance(payload["scope"], str):
        errors.append("invalid schema or scope")
    required_modes = payload["required_modes"]
    if (not isinstance(required_modes, list) or any(not isinstance(mode, str) for mode in required_modes)
            or set(required_modes) != MODES or len(required_modes) != len(MODES)):
        errors.append("required_modes must list each supported real-feed/replay/shadow/paper/dry-run/live-cash mode once")
    entries = payload["capabilities"]
    if not isinstance(entries, list) or not entries:
        return errors + ["capabilities must be a nonempty list"]
    seen: set[str] = set()
    seen_modes: set[str] = set()
    for index, entry in enumerate(entries):
        label = f"capability {index}"
        if not isinstance(entry, dict) or set(entry) != {
                "id", "owner_role", "maturity", "execution_mode", "fidelity", "evidence",
                "source", "tests", "blockers"}:
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
        if not isinstance(entry["execution_mode"], str) or entry["execution_mode"] not in MODES:
            errors.append(f"{label}: invalid execution mode")
        if not isinstance(entry["fidelity"], str) or not entry["fidelity"].strip():
            errors.append(f"{label}: fidelity statement is required")
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
        evidence = entry["evidence"]
        if not isinstance(evidence, list) or not evidence or len(set(map(str, evidence))) != len(evidence):
            errors.append(f"{label}: evidence must contain unique paths")
            continue
        for relative in evidence:
            if not isinstance(relative, str) or "\\" in relative or Path(relative).is_absolute():
                errors.append(f"{label}: invalid evidence path {relative!r}")
                continue
            resolved = (root / relative).resolve()
            if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
                errors.append(f"{label}: missing evidence path {relative}")
            if not isinstance(entry["tests"], list) or relative not in entry["tests"]:
                errors.append(f"{label}: evidence path must also be a tested module: {relative}")
        if isinstance(entry["execution_mode"], str) and entry["execution_mode"] in MODES:
            seen_modes.add(entry["execution_mode"])
    if seen_modes != MODES:
        errors.append(f"capabilities do not cover every required mode: {sorted(MODES - seen_modes)}")
    return errors


if __name__ == "__main__":
    problems = validate(MATRIX, Path("."))
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        raise SystemExit(1)
    print("Capability matrix paths and conservative statuses verified")
