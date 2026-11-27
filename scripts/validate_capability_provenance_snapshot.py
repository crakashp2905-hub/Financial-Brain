"""Validate the captured capability/branch provenance graph against its PR snapshot."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/edge-program/capability-provenance.v1.json"
PR_SNAPSHOT = ROOT / "docs/edge-program/open-pr-snapshot-2026-11-26.json"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
EXPECTED = {"main", "PR-34", "PR-36", "PR-37", "PR-38", "PR-39", "PR-40"}


def validate(manifest: dict[str, Any], snapshot: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema") != "fb.capability_provenance.v2":
        return ["unsupported capability provenance schema"]
    sources = manifest.get("sources", [])
    by_id = {row.get("source_id"): row for row in sources if isinstance(row, dict)}
    if set(by_id) != EXPECTED or len(sources) != len(EXPECTED):
        errors.append("source set must contain exactly main and PRs 34, 36, 37, 38, 39, 40")
    main_sha = by_id.get("main", {}).get("commit_sha")
    if not isinstance(main_sha, str) or not SHA40.fullmatch(main_sha):
        errors.append("captured main commit SHA is invalid")
    pr_rows = {row.get("number"): row for row in snapshot.get("pull_requests", [])}
    for number in (34, 36, 37, 38, 39):
        source = by_id.get(f"PR-{number}", {})
        pr = pr_rows.get(number)
        if not pr:
            errors.append(f"PR #{number} missing from open-PR snapshot")
            continue
        if source.get("state") != "open" or source.get("merged") is not False:
            errors.append(f"PR #{number} must remain labeled open/unmerged")
        if source.get("base", {}).get("sha") != pr.get("base", {}).get("sha"):
            errors.append(f"PR #{number} base SHA differs from open-PR snapshot")
        if source.get("head", {}).get("sha") != pr.get("head", {}).get("sha"):
            errors.append(f"PR #{number} head SHA differs from open-PR snapshot")
    for source in sources:
        registry = source.get("capability_registry", {})
        sha = registry.get("git_blob_sha1")
        if registry.get("path") != "docs/edge-program/capability-ownership.v1.json" or not isinstance(sha, str) or not SHA40.fullmatch(sha):
            errors.append(f"{source.get('source_id')} capability registry blob binding is invalid")
        if not isinstance(registry.get("capability_count"), int) or not isinstance(registry.get("module_count"), int):
            errors.append(f"{source.get('source_id')} capability inventory counts are missing")
    expected_edges = {("PR-37", "PR-36"), ("PR-36", "PR-38"), ("PR-40", "main")}
    actual_edges = {(edge.get("from"), edge.get("to")) for edge in manifest.get("dependency_edges", []) if edge.get("verified") is True}
    if actual_edges != expected_edges:
        errors.append("verified dependency graph does not match the exact base/head and merge ancestry evidence")
    if by_id.get("PR-36", {}).get("base", {}).get("sha") != by_id.get("PR-37", {}).get("head", {}).get("sha"):
        errors.append("PR #36 base must equal PR #37 head")
    if by_id.get("PR-38", {}).get("base", {}).get("sha") != by_id.get("PR-36", {}).get("head", {}).get("sha"):
        errors.append("PR #38 base must equal PR #36 head")
    pr40 = by_id.get("PR-40", {})
    if not pr40.get("merged") or pr40.get("merge_commit_sha") != pr40.get("current_main_relation", {}).get("merge_base_sha"):
        errors.append("PR #40 merged-ancestor proof is inconsistent")
    return errors


def main() -> int:
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        snapshot = json.loads(PR_SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL cannot read provenance artifacts: {exc}")
        return 1
    errors = validate(manifest, snapshot)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print("capability provenance valid: main and six requested PR sources; dependency edges are SHA-bound")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
