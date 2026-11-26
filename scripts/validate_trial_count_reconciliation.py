"""Verify the Q4 protected-trial count against immutable ledger bytes and pins."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs/trials/ledger.jsonl"
PINS = ROOT / "docs/forward-edge/protected-pins.json"
INDEX = ROOT / "docs/edge-program/v1.4-traceability-index.json"
AUDIT = ROOT / "docs/audits/five-desk-v1_4/audit.json"
REPORT = ROOT / "docs/edge-program/trial-count-reconciliation.v1.json"


def validate() -> list[str]:
    errors: list[str] = []
    raw = LEDGER.read_bytes()
    pins: dict[str, Any] = json.loads(PINS.read_text(encoding="utf-8"))
    path = LEDGER.relative_to(ROOT).as_posix()
    normalized = raw.replace(b"\r\n", b"\n") if path in pins.get("normalize_eol", []) else raw
    pinned_sha = pins.get("files", {}).get(path)
    normalized_sha = hashlib.sha256(normalized).hexdigest()
    if normalized_sha != pinned_sha:
        errors.append("protected ledger bytes do not match their pinned normalized SHA-256")

    records = [json.loads(line) for line in normalized.splitlines() if line.strip()]
    kinds = Counter(record.get("kind") for record in records)
    trials = [record for record in records if record.get("kind") == "trial"]
    verdicts = Counter(record.get("verdict") for record in trials)
    if len(records) != 14 or kinds != Counter({"history": 1, "trial": 13}):
        errors.append("ledger must contain 1 history row plus exactly 13 trial rows")
    if len(trials) != 13 or verdicts != Counter({"FAIL": 13}):
        errors.append("the 13 protected trial rows must all retain FAIL verdicts")

    index = json.loads(INDEX.read_text(encoding="utf-8"))
    historical_note = "14 failed 2026Q4 trials"
    if historical_note not in INDEX.read_text(encoding="utf-8"):
        errors.append("historical v1.4 index contradiction is no longer visible")
    if historical_note not in AUDIT.read_text(encoding="utf-8"):
        errors.append("historical source-audit contradiction is no longer visible")
    fb4 = next((row for row in index.get("tasks", []) if row.get("id") == "FB-0004"), None)
    if fb4 is None or historical_note not in fb4.get("note", ""):
        errors.append("FB-0004 does not retain the original contradictory survey note")

    report = json.loads(REPORT.read_text(encoding="utf-8"))
    expected = report.get("ledger", {})
    if expected.get("normalized_lf_sha256") != normalized_sha:
        errors.append("reconciliation report normalized ledger SHA differs from current pinned bytes")
    if expected.get("jsonl_record_count") != len(records):
        errors.append("reconciliation report total JSONL record count is stale")
    if expected.get("record_kinds") != dict(kinds):
        errors.append("reconciliation report record-kind counts are stale")
    if expected.get("protected_trial_count") != len(trials):
        errors.append("reconciliation report protected trial count is stale")
    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print("trial count reconciled: 14 JSONL rows = 1 history + 13 protected FAIL trials; pin matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
