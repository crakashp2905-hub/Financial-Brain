"""Verify the frozen v3 checklist baseline against its source commit and protected trials."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs/edge-program/checklist-baseline.v1.json"


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True,
                          capture_output=True, text=True, timeout=10).stdout.strip()


def validate() -> list[str]:
    report = json.loads(BASELINE.read_text(encoding="utf-8"))
    errors: list[str] = []
    if report.get("schema") != "fb.unified_checklist_baseline.v1":
        errors.append("unsupported baseline schema")
    commit = report["git"]["source_commit"]
    try:
        tree = git("rev-parse", f"{commit}^{{tree}}")
    except subprocess.CalledProcessError:
        errors.append("baseline source commit is absent from this repository")
        tree = ""
    if tree and tree != report["git"]["tree_sha"]:
        errors.append("baseline tree SHA differs from source commit")
    lock = report["dependencies"]["lockfile"]
    try:
        lock_bytes = subprocess.run(["git", "-C", str(ROOT), "show", f"{commit}:{lock}"],
                                    check=True, capture_output=True, timeout=10).stdout
    except subprocess.CalledProcessError:
        errors.append("dependency lock is absent at the baseline source commit")
        lock_bytes = b""
    if lock_bytes and hashlib.sha256(lock_bytes).hexdigest() != report["dependencies"]["commit_blob_sha256"]:
        errors.append("dependency lock blob hash differs from source commit")
    if not report["git"]["feature_branch"].startswith("codex/"):
        errors.append("baseline branch is not a dedicated Codex feature branch")
    tests = report["tests"]
    if tests["collected"] < 1 or tests["captured_for_commit"] != commit:
        errors.append("test collection evidence is invalid or tied to another commit")
    ci = report["ci"]
    if ci["source_commit"] != commit or ci["status"] not in {"in_progress", "completed", "unavailable"}:
        errors.append("CI evidence is invalid or tied to another commit")
    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print("Baseline commit/tree and dependency-lock hashes verify; CI state is recorded without promotion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
