"""Build or verify the frozen, hash-only evidence bundle for checklist v3.0."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# Allow the documented direct-script invocation from a clean checkout.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from financial_brain.forward_edge.evidence_bundle import (
    export_evidence_bundle,
    verify_evidence_bundle,
)

SNAPSHOT_COMMIT = "30f721248c0f2b8b5323aa9565197e70c3841c18"
SOURCE_PATHS = (
    ".github/workflows/ci.yml",
    "docs/edge-program/Financial_Brain_Unified_Master_Checklist_v3_0.md",
    "docs/edge-program/V3.0_PROGRESS.md",
    "docs/edge-program/capability-ownership.v1.json",
    "docs/edge-program/forward-engine-dependency-graph.v1.json",
    "docs/edge-program/historical-strategy-pins.v1.json",
    "docs/edge-program/market-data-sources.v1.json",
    "docs/edge-program/paper-engine-equivalence.v1.json",
    "docs/edge-program/PAPER_ENGINE_EQUIVALENCE.md",
    "docs/edge-program/release-semantics.v1.json",
    "docs/edge-program/reproducibility-contract.v1.json",
    "docs/edge-program/source-inventory.v2.json",
    "docs/edge-program/state-ownership.v1.json",
    "docs/edge-program/v3.0-baseline-2026-11-21.json",
    "docs/edge-program/v3.0-pr-graph-2026-11-21.json",
    "docs/edge-program/v3.0-status.json",
    "scripts/audit/paper_engine_equivalence.py",
    "scripts/validate_capability_ownership.py",
    "scripts/validate_historical_strategy_pins.py",
)
MANIFEST_PATH = Path("docs/edge-program/checklist-release-inputs.v1.json")
BUNDLE_PATH = Path("docs/edge-program/checklist-release-evidence-v3.0.zip")
REPORT_PATH = Path("docs/edge-program/checklist-release-bundle-report.v1.json")
GENERATOR_PATH = Path("scripts/build_checklist_evidence_bundle.py")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_blob(root: Path, path: str) -> bytes:
    return subprocess.check_output(
        ["git", "show", f"{SNAPSHOT_COMMIT}:{path}"], cwd=root, stderr=subprocess.STDOUT
    )


def build_artifacts(root: Path) -> tuple[bytes, bytes, bytes]:
    root = root.resolve()
    resolved = subprocess.check_output(
        ["git", "rev-parse", f"{SNAPSHOT_COMMIT}^{{commit}}"], cwd=root,
        stderr=subprocess.STDOUT,
    ).decode("ascii").strip()
    if resolved != SNAPSHOT_COMMIT:
        raise RuntimeError("evidence snapshot commit must be a full immutable SHA")

    payloads: dict[str, bytes] = {}
    source_names: dict[str, str] = {}
    for source_path in SOURCE_PATHS:
        # Archive names use a conservative portable path grammar; retain the
        # original checkout path in the input manifest for traceability.
        archive_path = ("config/github/workflows/ci.yml"
                        if source_path == ".github/workflows/ci.yml" else source_path)
        payloads[archive_path] = _git_blob(root, source_path)
        source_names[archive_path] = source_path
    generator = root / GENERATOR_PATH
    if not generator.is_file() or generator.is_symlink():
        raise RuntimeError("bundle generator must be a regular local file")
    # Windows checkouts may materialize CRLF even though Git stores LF. Keep
    # the archived generator bytes portable so the ZIP hash is cross-platform.
    payloads[GENERATOR_PATH.as_posix()] = generator.read_bytes().replace(b"\r\n", b"\n")
    manifest = {
        "schema": "fb.checklist-release-inputs.v1",
        "snapshot_commit": SNAPSHOT_COMMIT,
        "timestamp_attestation": "NONE",
        "integrity_scope": "SHA-256 content integrity only; no independent timestamp or external signature is asserted.",
        "inputs": [
            {"path": path, "source_path": source_names.get(path, path),
             "size_bytes": len(data), "sha256": _sha256(data)}
            for path, data in sorted(payloads.items())
        ],
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    with tempfile.TemporaryDirectory(prefix="fb-checklist-bundle-", dir=root) as tmp:
        temp_root = Path(tmp)
        files: dict[str, Path] = {}
        for name, data in payloads.items():
            staged = temp_root / name
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(data)
            files[name] = staged
        staged_manifest = temp_root / MANIFEST_PATH
        staged_manifest.parent.mkdir(parents=True, exist_ok=True)
        staged_manifest.write_bytes(manifest_bytes)
        files[MANIFEST_PATH.as_posix()] = staged_manifest

        generated = temp_root / "release-evidence.zip"
        report = export_evidence_bundle(
            files=files,
            output=generated,
            reproduction_command=("python", "scripts/build_checklist_evidence_bundle.py", "--check"),
        )
        verified = verify_evidence_bundle(generated)
        if (verified.bundle_sha256 != report.bundle_sha256
                or verified.signature_status != "UNSIGNED"):
            raise RuntimeError("generated evidence bundle failed local hash verification")
        bundle_bytes = generated.read_bytes()

    sidecar: dict[str, Any] = {
        "schema": "fb.checklist-release-bundle-report.v1",
        "snapshot_commit": SNAPSHOT_COMMIT,
        "bundle_path": BUNDLE_PATH.as_posix(),
        "bundle_sha256": report.bundle_sha256,
        "manifest_sha256": report.manifest_sha256,
        "file_count": report.file_count,
        "signature_status": report.signature_status,
        "timestamp_attestation": "NONE",
        "claim": "Locally reproducible SHA-256 integrity package. It is not independently timestamped or signed.",
    }
    report_bytes = (json.dumps(sidecar, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return manifest_bytes, bundle_bytes, report_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify committed evidence outputs are reproducible")
    args = parser.parse_args()
    root = ROOT
    expected = build_artifacts(root)
    targets = {
        root / MANIFEST_PATH: expected[0],
        root / BUNDLE_PATH: expected[1],
        root / REPORT_PATH: expected[2],
    }
    text_outputs = {root / MANIFEST_PATH, root / REPORT_PATH}
    mismatches = []
    for path, data in targets.items():
        current = path.read_bytes() if path.is_file() else None
        if current is not None and path in text_outputs:
            current = current.replace(b"\r\n", b"\n")
        if current != data:
            mismatches.append(str(path.relative_to(root)))
    if args.check:
        if mismatches:
            raise SystemExit("checklist evidence bundle is stale: " + ", ".join(mismatches))
        print("checklist evidence bundle hashes and bytes verified")
        return 0
    for path, data in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    print("wrote checklist release evidence bundle and hash report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
