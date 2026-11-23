"""Validate the captured v3 PR dependency and changed-path overlap graph."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs/edge-program/v3.0-pr-graph-2026-11-23-r3.json"
EXPECTED = {"34", "36", "37", "38", "39", "40", "87"}


def validate_snapshot(graph: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if graph.get("schema") != "fb.v3_pr_dependency_graph.v3":
        return ["unsupported PR dependency graph schema"]
    nodes = graph.get("nodes")
    if not isinstance(nodes, dict) or set(nodes) != EXPECTED:
        return ["PR graph must contain exactly PRs 34, 36, 37, 38, 39, 40 and 87"]

    paths: dict[str, set[str]] = {}
    for key, node in nodes.items():
        if not isinstance(node, dict) or node.get("number") != int(key):
            errors.append(f"PR {key} node identity is inconsistent")
            continue
        files = node.get("changed_files")
        if not isinstance(files, list) or any(not isinstance(path, str) or not path for path in files):
            errors.append(f"PR {key} changed-file list is malformed")
            continue
        if len(files) != len(set(files)):
            errors.append(f"PR {key} changed-file list has duplicates")
        paths[key] = set(files)
        if node.get("state") not in {"open", "closed"} or type(node.get("merged")) is not bool:
            errors.append(f"PR {key} state is malformed")
        if node.get("merged") and node.get("state") != "closed":
            errors.append(f"merged PR {key} must be closed")
        if type(node.get("review_count")) is not int or node["review_count"] < 0:
            errors.append(f"PR {key} review count is malformed")

    for key, node in nodes.items():
        if not isinstance(node, dict):
            continue
        expected_dependencies = sorted(
            int(other_key) for other_key, other in nodes.items()
            if other_key != key and isinstance(other, dict)
            and (node.get("base") == other.get("head")
                 or node.get("base_sha") == other.get("head_sha"))
        )
        if sorted(node.get("depends_on", [])) != expected_dependencies:
            errors.append(f"PR {key} dependency edges do not match its exact base ref")

    expected_overlaps: dict[tuple[int, int], set[str]] = {}
    for left in sorted(paths, key=int):
        for right in sorted(paths, key=int):
            if int(left) >= int(right):
                continue
            common = paths[left] & paths[right]
            if common:
                expected_overlaps[(int(left), int(right))] = common
    actual_overlaps: dict[tuple[int, int], set[str]] = {}
    for edge in graph.get("overlaps", []):
        if not isinstance(edge, dict):
            errors.append("overlap edge must be an object")
            continue
        pair = (edge.get("from"), edge.get("to"))
        files = edge.get("shared_paths")
        if (not all(type(value) is int for value in pair)
                or not isinstance(files, list)
                or edge.get("shared_path_count") != len(files)):
            errors.append("overlap edge identity or count is malformed")
            continue
        if edge.get("validation_transfer") != "NONE; shared paths require review against the exact target head":
            errors.append(f"overlap {pair} incorrectly implies inherited validation")
        actual_overlaps[pair] = set(files)
    if actual_overlaps != expected_overlaps:
        errors.append("overlap edges do not exactly match the changed-file lists")
    if "PRs 34 and 36-40" not in graph.get("scope", ""):
        errors.append("snapshot scope must state the required PR range")
    return errors


def main() -> int:
    try:
        graph = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL cannot read PR dependency snapshot: {exc}")
        return 1
    errors = validate_snapshot(graph)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print("PR dependency and overlap snapshot is internally consistent; validation is not inherited across heads.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
