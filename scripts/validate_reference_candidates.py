"""Verify frozen source fingerprints and conservative labels for reference candidates."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/forward-edge/reference-candidates.v1.json"
REQUIRED = {"H005", "H003", "P3"}


def validate() -> list[str]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    errors: list[str] = []
    if data.get("schema") != "fb.forward_edge.reference_candidates.v1":
        errors.append("unsupported reference-candidate schema")
    policy = data.get("claim_policy", {})
    if policy.get("prospective_edge_confirmed") is not False:
        errors.append("reference candidates must not be marked as confirmed prospective edges")
    if policy.get("allowed_label") != "HISTORICAL_CANDIDATE_ONLY":
        errors.append("reference candidate label must remain historical-only")
    sources = {row.get("path"): row.get("sha256_lf") for row in data.get("source_artifacts", [])}
    for rel, expected in sources.items():
        path = ROOT / rel
        actual = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        if not isinstance(expected, str) or len(expected) != 64 or expected != actual:
            errors.append(f"source fingerprint mismatch: {rel}")
    candidates = data.get("candidates", [])
    ids = {row.get("id") for row in candidates}
    if ids != REQUIRED or len(candidates) != len(REQUIRED):
        errors.append(f"candidate set must be exactly {sorted(REQUIRED)}")
    for row in candidates:
        candidate_id = row.get("id")
        if row.get("evidence_tier") != "HISTORICAL_CANDIDATE_ONLY":
            errors.append(f"{candidate_id}: evidence tier must remain historical-only")
        if row.get("prospective_status") != "NOT_CONFIRMED":
            errors.append(f"{candidate_id}: prospective status must remain unconfirmed")
        if not row.get("reference_version") or not row.get("implementation") or not row.get("source_paths"):
            errors.append(f"{candidate_id}: version, implementation and source paths are required")
        if any(path not in sources for path in row.get("source_paths", [])):
            errors.append(f"{candidate_id}: all candidate source paths must be fingerprinted")
    return errors


def main() -> int:
    errors = validate()
    if errors:
        raise SystemExit("\n".join(errors))
    print("H005/H003/P3 references are fingerprinted and remain historical-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
