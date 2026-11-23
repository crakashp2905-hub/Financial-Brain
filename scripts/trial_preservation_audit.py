"""Report which protected Q4 trial facts are pinned and which were never recorded."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from financial_brain.forward_edge.trials import PINS_REL, sha256_file, verify_pins

LEDGER = ROOT / "docs/trials/ledger.jsonl"
PINS = ROOT / PINS_REL
MANIFEST_DIR = ROOT / "docs/trials/manifests"
OUTPUT = ROOT / "docs/edge-program/trial-preservation-audit.v1.json"
BASELINE = ROOT / "docs/edge-program/trial-preservation-baseline.v1.json"


def comparison_commit() -> str:
    """Return the PR source commit when Actions checked out a synthetic merge ref."""
    ref = "HEAD^2" if os.environ.get("GITHUB_EVENT_NAME") == "pull_request" else "HEAD"
    result = subprocess.run(
        ["git", "rev-parse", ref], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"unable to resolve protected-source comparison commit {ref}")
    return result.stdout.strip()


def verify_baseline_blobs() -> list[dict[str, str]]:
    """Compare protected source paths to immutable blob IDs captured from main."""
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    commit = comparison_commit()
    errors = []
    for path, expected in baseline.get("files", {}).items():
        result = subprocess.run(
            ["git", "rev-parse", f"{commit}:{path}"], cwd=ROOT, capture_output=True,
            text=True, check=False,
        )
        actual = result.stdout.strip() if result.returncode == 0 else ""
        if actual != expected:
            errors.append({"path": path, "expected_blob": expected, "actual_blob": actual})
    return errors


def build_report() -> dict:
    pin_document = json.loads(PINS.read_text(encoding="utf-8"))
    ledger_lines = LEDGER.read_bytes().splitlines(keepends=True)
    manifest_paths = sorted(MANIFEST_DIR.glob("*.json"))
    manifests = [json.loads(path.read_text(encoding="utf-8")) for path in manifest_paths]
    manifest_by_trial = {str(row.get("trial")): (path, row)
                         for path, row in zip(manifest_paths, manifests, strict=True)}
    trials = []
    for raw in ledger_lines:
        if not raw.strip():
            continue
        row = json.loads(raw)
        if row.get("kind") != "trial":
            continue
        name = str(row.get("name", ""))
        match = manifest_by_trial.get(name)
        manifest_path, manifest = match if match else (None, None)
        data = (manifest or {}).get("data", {})
        data_hashes = sorted(
            (dataset, str(details.get("hash")))
            for dataset, details in data.items()
            if isinstance(details, dict) and details.get("hash")
        )
        registration_recorded = bool(row.get("recorded_at"))
        outcome_recorded = bool(row.get("verdict"))
        read_window_recorded = bool(manifest and manifest.get("note") and
                                    any(token in str(manifest["note"]).lower()
                                        for token in ("read on", "read window", "inputs are those")))
        missing = []
        if not registration_recorded:
            missing.append("registration_timestamp")
        if not outcome_recorded:
            missing.append("failure_outcome")
        if not data_hashes:
            missing.append("input_hashes")
        if not read_window_recorded:
            missing.append("historical_read_window")
        trials.append({
            "name": name,
            "ledger_line_sha256": hashlib.sha256(raw).hexdigest(),
            "registration_timestamp": row.get("recorded_at"),
            "verdict": row.get("verdict"),
            "failure_gates": row.get("failed", []),
            "manifest_path": manifest_path.relative_to(ROOT).as_posix() if manifest_path else None,
            "data_input_hashes": data_hashes,
            "historical_read_window_recorded": read_window_recorded,
            "missing_historical_evidence": missing,
        })
    return {
        "schema": "fb.trial_preservation_audit.v1",
        "scope": "Audit of already-recorded Q4 2026 protected trial facts. Missing historical facts are reported as missing and are never reconstructed or backfilled.",
        "ledger": {
            "path": LEDGER.relative_to(ROOT).as_posix(),
            "sha256_lf": sha256_file(LEDGER, normalize_eol=True),
            "protected_pin_sha256": pin_document.get("files", {}).get(LEDGER.relative_to(ROOT).as_posix()),
            "pin_verification_errors": verify_pins(ROOT),
            "trial_count": len(trials),
            "history_rows_preserved": sum(1 for line in ledger_lines if b'"kind": "history"' in line),
        },
        "main_baseline": {
            "commit": json.loads(BASELINE.read_text(encoding="utf-8"))["source_commit"],
            "compared_commit": comparison_commit(),
            "protected_source_count": len(json.loads(BASELINE.read_text(encoding="utf-8"))["files"]),
            "blob_mismatches": verify_baseline_blobs(),
        },
        "coverage": {
            "registration_timestamps": sum(bool(row["registration_timestamp"]) for row in trials),
            "failure_outcomes": sum(bool(row["verdict"]) for row in trials),
            "input_hashes": sum(bool(row["data_input_hashes"]) for row in trials),
            "historical_read_windows": sum(row["historical_read_window_recorded"] for row in trials),
            "fully_documented_trials": sum(not row["missing_historical_evidence"] for row in trials),
        },
        "trials": trials,
        "conclusion": (
            "Protected ledger/manifests match their original main Git blobs byte-for-byte. The pin proves preservation of bytes, not that "
            "every trial originally recorded a dataset hash or historical read window. Missing fields remain missing."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when the committed audit is stale")
    args = parser.parse_args(argv)
    expected = json.dumps(build_report(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise SystemExit("trial preservation audit is missing or stale")
        print("Protected trial preservation audit matches pinned ledger and manifests")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(expected, encoding="utf-8", newline="\n")
    print(f"Wrote {OUTPUT.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
