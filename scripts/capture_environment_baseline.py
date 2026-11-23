"""Capture and verify an environment-bound checklist baseline without secrets."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "requirements-lock.txt"
SOURCE_MATRIX = ROOT / "docs/edge-program/market-data-sources.v1.json"


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True,
                          capture_output=True, text=True, timeout=15).stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _scheduled_tasks() -> dict[str, Any]:
    """Record declared repo schedules; do not imply external scheduler visibility."""
    workflows: list[dict[str, Any]] = []
    workflow_root = ROOT / ".github/workflows"
    for path in sorted(workflow_root.glob("*.yml")) + sorted(workflow_root.glob("*.yaml")):
        content = path.read_text(encoding="utf-8")
        if "schedule:" in content:
            workflows.append({"workflow": path.relative_to(ROOT).as_posix(),
                              "checkout_path": "repository root (GitHub Actions checkout)",
                              "workflow_sha256": _sha256(path)})
    # Task Scheduler and external CI agents are outside repository state. Probe
    # with the OS-native query, but never turn query failure into an empty list.
    probe: dict[str, Any]
    if os.name == "nt":
        try:
            result = subprocess.run(["schtasks.exe", "/query", "/fo", "CSV", "/v"],
                                    capture_output=True, text=True, timeout=20)
            if result.returncode == 0:
                rows = list(csv.DictReader(result.stdout.splitlines()))
                probe = {"status": "OBSERVED", "task_count": len(rows),
                         "financial_brain_candidates": [
                             {"task_name": row.get("TaskName"), "command": row.get("Task To Run")}
                             for row in rows if "financial-brain" in str(row).lower()
                             or "finance-brain" in str(row).lower()]}
            else:
                probe = {"status": "UNAVAILABLE", "reason": result.stderr.strip() or
                         f"schtasks exited {result.returncode}"}
        except (OSError, subprocess.TimeoutExpired) as exc:
            probe = {"status": "UNAVAILABLE", "reason": str(exc)}
    else:
        probe = {"status": "NOT_PROBED", "reason": "external scheduler inventory is host-specific"}
    handoff = ROOT / "docs/HANDOFF.md"
    handoff_text = handoff.read_text(encoding="utf-8") if handoff.is_file() else ""
    external_tasks = ([{"task_name": "financial-brain-resume", "declared_in": "docs/HANDOFF.md",
                        "checkout_path": "UNVERIFIED"}]
                      if "financial-brain-resume" in handoff_text else [])
    return {"repository_workflows": workflows, "host_scheduler": probe,
            "documented_external_tasks": external_tasks,
            "complete_checkout_paths_verified": (
                probe["status"] == "OBSERVED" and not probe.get("financial_brain_candidates")
                and not external_tasks)}


def capture(*, ignore_paths: tuple[Path, ...] = ()) -> dict[str, Any]:
    source_matrix = json.loads(SOURCE_MATRIX.read_text(encoding="utf-8"))
    entitlements = [{"source_id": source["id"], "status": source["status"],
                     "retention_status": source["retention"]["status"],
                     "runtime_ready": source["access"]["runtime_ready"],
                     "credential_presence": {flag["name"]: flag["present"]
                                             for flag in source["access"]["credential_flags"]}}
                    for source in source_matrix["sources"]]
    raw_status = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain=v1",
                                 "--untracked-files=all"], check=True, capture_output=True,
                                text=True, timeout=15).stdout
    ignored = {path.resolve().relative_to(ROOT).as_posix() for path in ignore_paths
               if path.resolve().is_relative_to(ROOT)}
    status_rows = [line for line in raw_status.splitlines()
                   if line[3:].strip().strip('"').replace("\\", "/") not in ignored]
    status = "\n".join(status_rows)
    return {
        "schema": "fb.environment_baseline.v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "current_date_local": datetime.now().astimezone().date().isoformat(),
        "repository": {"root": str(ROOT), "branch": _git("branch", "--show-current"),
                       "commit_sha": _git("rev-parse", "HEAD"),
                       "tree_sha": _git("rev-parse", "HEAD^{tree}"),
                       "working_tree_clean": not bool(status),
                       "working_tree_porcelain": status.splitlines()},
        "runtime": {"python": sys.version, "python_executable": sys.executable,
                    "os": platform.platform(), "architecture": platform.machine()},
        "dependencies": {"lock_path": LOCK.relative_to(ROOT).as_posix(),
                         "lock_sha256": _sha256(LOCK)},
        "datasource_entitlements": {"inventory_path": SOURCE_MATRIX.relative_to(ROOT).as_posix(),
                                    "inventory_sha256": _sha256(SOURCE_MATRIX),
                                    "sources": entitlements},
        "scheduled_tasks": _scheduled_tasks(),
    }


def compare_capture(current: dict[str, Any], expected: dict[str, Any]) -> tuple[list[str], bool]:
    """Return captured-field mismatches and whether scheduled paths are verified."""
    mismatches = [key for key in ("repository", "runtime", "dependencies",
                                  "datasource_entitlements", "scheduled_tasks")
                  if current.get(key) != expected.get(key)]
    verified = current.get("scheduled_tasks", {}).get("complete_checkout_paths_verified") is True
    return mismatches, verified


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write a new JSON capture")
    parser.add_argument("--check", type=Path, help="compare current immutable fields with a capture")
    args = parser.parse_args()
    if args.output and args.check:
        parser.error("choose --output or --check")
    current = capture(ignore_paths=tuple(path for path in (args.output, args.check) if path is not None))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"captured environment baseline: {args.output}")
        return 0
    if args.check:
        expected = json.loads(args.check.read_text(encoding="utf-8"))
        mismatches, scheduled_paths_verified = compare_capture(current, expected)
        if mismatches:
            print("FAIL unrecorded environment mismatch: " + ", ".join(mismatches))
            return 1
        if not scheduled_paths_verified:
            print("FAIL scheduled task checkout paths are not fully verified")
            return 1
        print("environment baseline matches; scheduled checkout paths are verified")
        return 0
    print(json.dumps(current, indent=2, sort_keys=True))
    if not current["scheduled_tasks"]["complete_checkout_paths_verified"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
