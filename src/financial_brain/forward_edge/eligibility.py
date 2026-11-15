"""Hash-pinned strategy eligibility catalogue for prospective and descriptive use.

This catalogue describes eligibility; it does not turn a strategy into a proven
edge or authorize orders. Changes require an explicit new catalogue version.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STATUSES = frozenset({"FAILED", "EXPLORATORY", "SHADOW_ONLY", "PAPER_ELIGIBLE",
                      "FROZEN_CHALLENGER", "VERIFIED", "RETIRED"})
SCHEMA = "fb.forward-edge.eligibility.v1"


class EligibilityError(ValueError):
    """Eligibility catalogue failed validation or immutability checks."""


@dataclass(frozen=True)
class Candidate:
    strategy_id: str
    status: str
    source_type: str
    source_ref: str
    source_sha256: str
    exclusion_reason: str

    def as_dict(self) -> dict[str, str]:
        return {"strategy_id": self.strategy_id, "status": self.status, "source_type": self.source_type,
                "source_ref": self.source_ref, "source_sha256": self.source_sha256,
                "exclusion_reason": self.exclusion_reason}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_catalogue(repo_root: Path) -> dict[str, Any]:
    """Build a deterministic, conservative catalogue from the checked-in source registries."""
    root = Path(repo_root)
    external_path = root / "docs/sources/algorithm-source-registry.json"
    external_bytes = external_path.read_bytes()
    external = json.loads(external_bytes)
    external_hash = _sha256(external_bytes)
    trial_path = root / "docs/trials/ledger.jsonl"
    trial_bytes = trial_path.read_bytes()
    trial_hash = _sha256(trial_bytes)
    trials = [json.loads(line) for line in trial_bytes.decode("utf-8").splitlines() if line.strip()]
    verdicts = {str(row["name"]): str(row.get("verdict", "UNVERDICTED")).upper()
                for row in trials if row.get("kind") == "trial" and row.get("name")}

    candidates: list[Candidate] = []
    for row in external.get("entries", []):
        if not isinstance(row, dict) or not row.get("id"):
            raise EligibilityError("external source registry contains an entry without an ID")
        status = "SHADOW_ONLY" if row.get("rights_status") == "licence_verified" else "EXPLORATORY"
        reason = ("External-source strategy is descriptive only until clean-room adaptation and local validation."
                  if row.get("source_type") == "strategy" else
                  "Source is an indicator, article or concept; it is not a validated executable strategy.")
        if row.get("rights_status") != "licence_verified":
            reason = "Reference-only rights; no third-party source code may be copied or executed. " + reason
        candidates.append(Candidate(str(row["id"]), status, "external_reference",
                                    "docs/sources/algorithm-source-registry.json#" + str(row["id"]),
                                    external_hash, reason))
    for name, verdict in sorted(verdicts.items()):
        status = "FAILED" if verdict == "FAIL" else "EXPLORATORY"
        reason = ("Historical preregistered trial failed; historical result remains binding and cannot be promoted by shadow results."
                  if status == "FAILED" else
                  "Historical record does not establish current prospective eligibility; review its original charter.")
        candidates.append(Candidate("trial:" + name, status, "historical_trial",
                                    "docs/trials/ledger.jsonl#" + name, trial_hash, reason))
    candidates.sort(key=lambda c: c.strategy_id)
    ids = [c.strategy_id for c in candidates]
    if len(ids) != len(set(ids)):
        raise EligibilityError("strategy IDs must be unique")
    body = {"schema": SCHEMA, "catalogue_version": 1,
            "sources": {"external_registry": {"path": "docs/sources/algorithm-source-registry.json", "sha256": external_hash},
                        "historical_trials": {"path": "docs/trials/ledger.jsonl", "sha256": trial_hash}},
            "candidates": [c.as_dict() for c in candidates]}
    body["catalogue_sha256"] = _sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return body


def validate_catalogue(catalogue: dict[str, Any], repo_root: Path) -> None:
    if catalogue.get("schema") != SCHEMA or catalogue.get("catalogue_version") != 1:
        raise EligibilityError("unsupported catalogue schema or version")
    candidates = catalogue.get("candidates")
    if not isinstance(candidates, list):
        raise EligibilityError("candidates must be a list")
    ids: set[str] = set()
    for row in candidates:
        if not isinstance(row, dict):
            raise EligibilityError("candidate must be an object")
        required = ("strategy_id", "status", "source_type", "source_ref", "source_sha256", "exclusion_reason")
        if any(not isinstance(row.get(k), str) or not row[k].strip() for k in required):
            raise EligibilityError("candidate needs status, source lineage and exclusion reason")
        if row["status"] not in STATUSES:
            raise EligibilityError(f"unknown status for {row['strategy_id']}")
        if row["strategy_id"] in ids:
            raise EligibilityError(f"duplicate strategy ID {row['strategy_id']}")
        ids.add(row["strategy_id"])
        if len(row["source_sha256"]) != 64:
            raise EligibilityError(f"missing source hash for {row['strategy_id']}")
    expected = build_catalogue(repo_root)
    if catalogue != expected:
        raise EligibilityError("catalogue or source lineage changed; issue a reviewed new catalogue version")


def write_catalogue(repo_root: Path, output: Path) -> dict[str, Any]:
    """Write a new catalogue exactly once; an existing file cannot be rewritten in place."""
    output = Path(output)
    if output.exists():
        raise EligibilityError("catalogue is immutable; choose a new versioned path")
    catalogue = build_catalogue(repo_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(catalogue, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return catalogue
