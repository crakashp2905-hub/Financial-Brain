"""Verify immutable historical strategy reference pins and claim guardrails."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
EXPECTED = {"H003", "H005", "P3"}


def source_at_commit(root: Path, commit: str, path: str) -> bytes:
    return subprocess.check_output(
        ["git", "show", f"{commit}:{path}"], cwd=root, stderr=subprocess.STDOUT
    )


def validate(root: Path) -> list[str]:
    document = json.loads(
        (root / "docs/edge-program/historical-strategy-pins.v1.json").read_text(encoding="utf-8")
    )
    errors: list[str] = []
    if document.get("schema") != "fb.historical-strategy-pins.v1":
        errors.append("unsupported historical strategy pin schema")
    policy = document.get("claim_policy", {})
    if (policy.get("evidence_class") != "HISTORICAL_RESEARCH_CANDIDATE"
            or policy.get("prospective_profitability") != "NOT_ESTABLISHED"
            or policy.get("confirmed_edge") is not False
            or policy.get("live_promotion_allowed") is not False):
        errors.append("reference pins must remain historical and non-promotable")
    commit = str(document.get("snapshot_commit", ""))
    try:
        resolved = subprocess.check_output(
            ["git", "rev-parse", f"{commit}^{{commit}}"], cwd=root, stderr=subprocess.STDOUT
        ).decode("ascii").strip()
    except subprocess.CalledProcessError:
        resolved = ""
        errors.append("snapshot commit is unavailable in this checkout")
    if resolved and resolved != commit:
        errors.append("snapshot_commit must be a full immutable commit SHA")

    source_hashes: dict[str, str] = {}
    for source in document.get("source_files", []):
        path = source.get("path", "")
        try:
            digest = hashlib.sha256(source_at_commit(root, commit, path)).hexdigest()
        except subprocess.CalledProcessError:
            errors.append(f"pinned source is missing at snapshot commit: {path}")
            continue
        source_hashes[path] = digest
        if digest != source.get("sha256"):
            errors.append(f"pinned source hash mismatch: {path}")

    candidates = document.get("candidates", [])
    ids = [candidate.get("id") for candidate in candidates]
    if set(ids) != EXPECTED or len(ids) != len(EXPECTED):
        errors.append("candidate pins must contain exactly H003, H005, and P3")
    for candidate in candidates:
        if not candidate.get("version_id") or not candidate.get("formula") or not candidate.get("entrypoint"):
            errors.append(f"candidate pin is incomplete: {candidate.get('id')}")
        if candidate.get("reference_status", "").startswith("CONFIRMED"):
            errors.append(f"candidate must not be labeled confirmed: {candidate.get('id')}")
        for path in candidate.get("source_paths", []) + candidate.get("evidence_paths", []):
            if path not in source_hashes:
                errors.append(f"candidate path lacks immutable source hash: {path}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    errors = validate(root)
    if args.json:
        print(json.dumps({"valid": not errors, "errors": errors}, sort_keys=True))
    elif errors:
        print("\n".join(errors))
    else:
        print("validated immutable historical pins for H003, H005, and P3")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
