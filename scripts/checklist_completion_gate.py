"""Audit COMPLETE checklist claims for documentation and executable evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "docs/edge-program/v3.0-status.json"
REPORT = ROOT / "docs/edge-program/completion-evidence-gaps.v1.json"
_DOC_REF = re.compile(r"(?<![\w.-])docs/[\w./-]+\.(?:md|json|yaml|yml)(?:#[\w.-]+)?")
_TEST_REF = re.compile(r"(?<![\w.-])tests/[\w./-]+\.py")


def _item_gaps(item: dict[str, Any], root: Path) -> list[str]:
    if item.get("status") != "COMPLETE":
        return []

    evidence = item.get("evidence", [])
    test_evidence = item.get("test_evidence", [])
    test_result = item.get("test_result")
    searchable = "\n".join(
        str(value)
        for value in [*evidence, *test_evidence, test_result]
        if value is not None
    )
    gaps: list[str] = []

    docs = [ref.split("#", 1)[0] for ref in _DOC_REF.findall(searchable)]
    if not any((root / ref).is_file() for ref in docs):
        gaps.append("missing_existing_documentation_reference")

    tests = [ref for ref in _TEST_REF.findall(searchable) if (root / ref).is_file()]
    if not (test_evidence or test_result):
        gaps.append("missing_recorded_test_evidence")
    if not tests:
        gaps.append("missing_existing_executable_test_artifact")

    artifacts = item.get("acceptance_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        gaps.append("missing_hashed_acceptance_artifact")
    else:
        valid_artifact = False
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            path = artifact.get("path")
            digest = artifact.get("sha256")
            if not isinstance(path, str) or not isinstance(digest, str):
                continue
            candidate = (root / path).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                continue
            if candidate.is_file() and hashlib.sha256(candidate.read_bytes()).hexdigest() == digest:
                valid_artifact = True
                break
        if not valid_artifact:
            gaps.append("acceptance_artifact_missing_or_hash_mismatch")
    return gaps


def build_report(status: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    gaps = [
        {"id": item.get("id"), "gaps": item_gaps}
        for item in status.get("items", [])
        if (item_gaps := _item_gaps(item, root))
    ]
    complete_count = sum(item.get("status") == "COMPLETE" for item in status.get("items", []))
    return {
        "schema_version": "1.0",
        "policy": {
            "complete_requires": [
                "an existing documentation reference",
                "recorded test evidence and an existing test file",
                "an existing acceptance artifact whose SHA-256 matches the ledger",
            ],
            "rule": "A module existing in the repository is not acceptance evidence by itself.",
        },
        "complete_items": complete_count,
        "items_with_evidence_gaps": len(gaps),
        "gap_count": sum(len(item["gaps"]) for item in gaps),
        "gaps": gaps,
    }


def validate_changed_complete_claims(
    current: dict[str, Any], base: dict[str, Any], root: Path = ROOT
) -> list[dict[str, Any]]:
    """Return changed COMPLETE rows that do not meet the evidence policy."""
    base_items = {item.get("id"): item for item in base.get("items", [])}
    failures: list[dict[str, Any]] = []
    for item in current.get("items", []):
        if item.get("status") != "COMPLETE":
            continue
        previous = base_items.get(item.get("id"))
        if previous == item:
            continue
        gaps = _item_gaps(item, root)
        if gaps:
            failures.append({"id": item.get("id"), "gaps": gaps})
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the committed gap report")
    parser.add_argument("--strict", action="store_true", help="fail when any COMPLETE item lacks required evidence")
    parser.add_argument("--base-commit", help="git commit used to reject newly unsupported COMPLETE claims")
    args = parser.parse_args()

    status = json.loads(STATUS.read_text(encoding="utf-8"))
    report = build_report(status)
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not REPORT.is_file() or REPORT.read_text(encoding="utf-8") != rendered:
            raise SystemExit("completion evidence gap report is stale; regenerate it")
        print(f"completion evidence audit current: {report['items_with_evidence_gaps']}/{report['complete_items']} COMPLETE items have gaps")
    else:
        REPORT.write_text(rendered, encoding="utf-8", newline="\n")
        print(f"wrote {REPORT.relative_to(ROOT).as_posix()}: {report['items_with_evidence_gaps']}/{report['complete_items']} COMPLETE items have evidence gaps")
    if args.base_commit:
        try:
            base_text = subprocess.run(
                ["git", "show", f"{args.base_commit}:docs/edge-program/v3.0-status.json"],
                cwd=ROOT,
                check=True,
                capture_output=True,
            ).stdout.decode("utf-8")
            base = json.loads(base_text)
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            raise SystemExit(f"unable to load base checklist status at {args.base_commit}: {exc}") from exc
        changed_gaps = validate_changed_complete_claims(status, base)
        if changed_gaps:
            print(json.dumps({"unsupported_changed_complete_claims": changed_gaps}, indent=2))
            return 2
        print("changed COMPLETE claims satisfy documentation, test and hashed acceptance-artifact requirements")
    if args.strict and report["items_with_evidence_gaps"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
