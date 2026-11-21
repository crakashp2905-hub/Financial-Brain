"""Validate the versioned, evidence-linked implementation-deviation register."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


class DeviationLogError(ValueError):
    pass


def validate(document: dict[str, Any], root: Path) -> None:
    if document.get("schema") != "fb.edge_program.deviations.v1":
        raise DeviationLogError("unsupported deviation-log schema")
    if document.get("audit_coverage") != "PARTIAL_KNOWN_DEVIATIONS":
        raise DeviationLogError("initial register must not claim exhaustive deviation coverage")
    protocol = document.get("protocol", {})
    protocol_path = str(protocol.get("path", ""))
    commit = str(protocol.get("source_commit", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not protocol_path:
        raise DeviationLogError("protocol source requires a full commit and repository path")
    result = subprocess.run(["git", "show", f"{commit}:{protocol_path}"], cwd=root,
                            capture_output=True, check=False)
    if result.returncode != 0 or hashlib.sha256(result.stdout).hexdigest() != protocol.get("sha256"):
        raise DeviationLogError("protocol source commit or SHA-256 does not match the recorded baseline")
    protocol_text = (root / protocol_path).read_text(encoding="utf-8")
    ids = set(re.findall(r"\*\*(PE-\d{4})\*\*", protocol_text))
    records = document.get("records")
    if not isinstance(records, list) or not records:
        raise DeviationLogError("deviation register must contain at least one record")
    seen: set[str] = set()
    for row in records:
        deviation_id = str(row.get("deviation_id", "")).strip()
        if not deviation_id or deviation_id in seen:
            raise DeviationLogError("deviation IDs must be non-empty and unique")
        seen.add(deviation_id)
        if row.get("protocol_task") not in ids:
            raise DeviationLogError(f"unknown protocol task for {deviation_id}")
        if row.get("record_type") not in {
            "evidence_gap", "integration_gap", "integration_variant", "accounting_gap", "recovery_gap",
            "checkpoint_gap", "execution-gate-gap", "coverage_gap", "evidence-integration-gap",
        }:
            raise DeviationLogError(f"unknown record type for {deviation_id}")
        author = row.get("author", {})
        version = row.get("implementation_version", {})
        if not author.get("id") or not author.get("type") or not version.get("git_commit") or not version.get("package_version"):
            raise DeviationLogError(f"author and implementation version are required for {deviation_id}")
        if version.get("git_commit") != commit:
            raise DeviationLogError(f"implementation version must bind to the recorded source commit for {deviation_id}")
        try:
            stamp = datetime.fromisoformat(str(row.get("recorded_at_utc", "")).replace("Z", "+00:00"))
        except ValueError as exc:
            raise DeviationLogError(f"invalid UTC clock for {deviation_id}") from exc
        if stamp.tzinfo is None or stamp.utcoffset() is None or not str(row["recorded_at_utc"]).endswith("Z"):
            raise DeviationLogError(f"clock must be an explicit UTC Z timestamp for {deviation_id}")
        if "NO_UPGRADE" not in str(row.get("effect_on_evidence_status", "")):
            raise DeviationLogError(f"evidence effect must state that status is not upgraded for {deviation_id}")
        if len(str(row.get("rollback", "")).strip()) < 20:
            raise DeviationLogError(f"a concrete rollback is required for {deviation_id}")
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or not evidence or any(not (root / path).is_file() for path in evidence):
            raise DeviationLogError(f"evidence paths must exist for {deviation_id}")


def load(root: Path) -> dict[str, Any]:
    document = json.loads((root / "docs/edge-program/deviations.v1.json").read_text(encoding="utf-8"))
    validate(document, root)
    return document


if __name__ == "__main__":
    repository = Path(__file__).resolve().parents[1]
    report = load(repository)
    print(f"validated {len(report['records'])} known deviations; full reconciliation remains open")
