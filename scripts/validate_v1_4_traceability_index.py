"""Validate the copied Five-Desk v1.4 task traceability snapshot."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "docs/edge-program/v1.4-traceability-index.json"
STATUS = ROOT / "docs/edge-program/v3.0-status.json"
TASK_STATUSES = {"DONE", "PARTIAL", "FOOTHOLD", "NOT_STARTED"}
EVIDENCE_BINDINGS = {"VERIFIED", "VERIFIED_TREE", "UNCOMMITTED_WORKTREE_PREFIX_ONLY"}
HEX40 = set("0123456789abcdef")


def validate(index: dict[str, Any], status_ledger: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if index.get("schema") != "fb.v1_4_traceability_index.v1":
        return ["unsupported v1.4 traceability-index schema"]
    if index.get("read_only_reference") is not True:
        errors.append("v1.4 traceability index must be declared a read-only historical reference")
    tasks = index.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 405:
        return errors + ["v1.4 traceability index must contain exactly 405 tasks"]
    ids = [task.get("id") for task in tasks if isinstance(task, dict)]
    current_fb = {item["id"] for item in status_ledger.get("items", [])
                  if isinstance(item, dict) and isinstance(item.get("id"), str)
                  and item["id"].startswith("FB-")}
    if len(ids) != 405 or len(set(ids)) != 405 or set(ids) != current_fb:
        errors.append("v1.4 task IDs must uniquely match all 405 canonical FB IDs")
    status_counts: Counter[str] = Counter()
    for task in tasks:
        if not isinstance(task, dict):
            errors.append("task row must be an object")
            continue
        task_id = task.get("id", "unknown")
        task_status = task.get("status")
        if task_status not in TASK_STATUSES:
            errors.append(f"{task_id} has an invalid historical status")
        else:
            status_counts[task_status] += 1
        expected_label = ("EXTERNAL" if task.get("dependency") in {"EXTERNAL", "OWNER", "TIME"}
                          else task_status)
        if task.get("traceability_label") != expected_label:
            errors.append(f"{task_id} dependency/status label is inconsistent")
        for group in ("source", "test"):
            evidence = task.get(group)
            if not isinstance(evidence, list):
                errors.append(f"{task_id} {group} evidence must be a list, including when empty")
                continue
            for record in evidence:
                if not isinstance(record, dict) or not record.get("path") or record.get("ref") is None:
                    errors.append(f"{task_id} has malformed {group} evidence")
                    continue
                binding = record.get("binding_status")
                if binding not in EVIDENCE_BINDINGS:
                    errors.append(f"{task_id} {group} evidence has an unrecognized SHA binding")
                elif binding in {"VERIFIED", "VERIFIED_TREE"}:
                    commit = record.get("commit_sha")
                    object_sha = record.get("object_sha")
                    expected_kind = "blob" if binding == "VERIFIED" else "tree"
                    if (not isinstance(commit, str) or len(commit) != 40 or set(commit) - HEX40
                            or not isinstance(object_sha, str) or len(object_sha) != 40
                            or set(object_sha) - HEX40 or record.get("object_kind") != expected_kind):
                        errors.append(f"{task_id} {group} evidence lacks full commit/object SHA")
                elif (record.get("ref") != "worktree" or record.get("commit_sha") is not None
                      or record.get("blob_sha") is not None):
                    errors.append(f"{task_id} worktree-only evidence must not claim a commit SHA")
    if dict(status_counts) != index.get("by_status"):
        errors.append("historical status counts do not match the 405 task rows")
    source_audit = index.get("source_audit", {})
    for key in ("commit_sha", "blob_sha"):
        value = source_audit.get(key)
        if not isinstance(value, str) or len(value) != 40 or set(value) - HEX40:
            errors.append(f"source audit {key} must be a full Git SHA")
    if index.get("depth") != "module survey; acceptance tests not run":
        errors.append("historical audit depth must retain the no-per-task-acceptance-tests limitation")
    return errors


def main() -> int:
    try:
        index = json.loads(INDEX.read_text(encoding="utf-8"))
        ledger = json.loads(STATUS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL cannot read traceability evidence: {exc}")
        return 1
    errors = validate(index, ledger)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    summary = index["binding_summary"]
    print(f"v1.4 index valid: 405 tasks; {summary['verified_full_source_or_test_objects']} fully bound source/test objects; "
          f"{summary['uncommitted_worktree_prefix_only']} uncommitted worktree references remain explicitly unresolved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
