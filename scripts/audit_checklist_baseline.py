"""Verify the frozen v3 checklist baseline against its source commit and protected trials."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs/edge-program/checklist-baseline.v1.json"
LATEST_BASELINE = ROOT / "docs/edge-program/v3.0-baseline-2026-11-23-r3.json"
HEX40 = set("0123456789abcdef")
HEX64 = HEX40


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True,
                          capture_output=True, text=True, timeout=10).stdout.strip()


def validate_latest_snapshot(report: dict) -> list[str]:
    """Check the item-level dated baseline's internal links and immutable source tree."""
    errors: list[str] = []
    if report.get("schema") != "fb.unified_checklist_baseline.v3":
        return ["unsupported latest baseline schema"]
    checkout = report.get("source_checkout", {})
    main = report.get("main", {})
    pr = report.get("pull_request", {})
    deps = report.get("dependencies", {})
    scheduler = report.get("active_scheduled_checkout", {})
    ci = report.get("ci", {})
    approvals = report.get("owner_approvals_and_branch_protection", {})

    for label, value in (("source commit", checkout.get("commit")),
                         ("source tree", checkout.get("tree")),
                         ("main commit", main.get("sha")),
                         ("main tree", main.get("tree")),
                         ("PR head", pr.get("head_sha")),
                         ("PR base", pr.get("base_sha"))):
        if not isinstance(value, str) or len(value) != 40 or set(value) - HEX40:
            errors.append(f"{label} must be a full lowercase Git SHA")
    if checkout.get("commit") != pr.get("head_sha"):
        errors.append("source checkout and PR head commits differ")
    if main.get("sha") != pr.get("base_sha"):
        errors.append("captured main and PR base commits differ")
    try:
        source_tree = git("rev-parse", f"{checkout['commit']}^{{tree}}")
    except (subprocess.CalledProcessError, KeyError):
        source_tree = ""
        errors.append("latest baseline source commit is absent from this repository")
    if source_tree and source_tree != checkout.get("tree"):
        errors.append("latest baseline source tree does not match its source commit")
    try:
        lock_blob = git("rev-parse", f"{checkout['commit']}:requirements-lock.txt")
    except (subprocess.CalledProcessError, KeyError):
        lock_blob = ""
        errors.append("latest baseline dependency lock is absent from its source commit")
    if lock_blob and lock_blob != deps.get("requirements_lock_git_blob"):
        errors.append("latest baseline dependency lock blob differs from its source commit")
    for name, value in (("pyproject hash", deps.get("pyproject_toml_sha256")),
                        ("lock hash", deps.get("requirements_lock_sha256_working_copy")),
                        ("CI workflow hash", ci.get("workflow_sha256"))):
        if not isinstance(value, str) or len(value) != 64 or set(value) - HEX64:
            errors.append(f"latest baseline {name} must be a SHA-256 digest")
    if pr.get("state") not in {"OPEN", "CLOSED"} or type(pr.get("merged")) is not bool:
        errors.append("latest baseline PR state is malformed")
    if pr.get("merged") is True and pr.get("state") != "CLOSED":
        errors.append("a merged PR cannot be recorded as open")
    if type(pr.get("review_submissions")) is not int or pr["review_submissions"] < 0:
        errors.append("latest baseline review count is malformed")
    if scheduler.get("status") not in {"VERIFIED", "UNVERIFIED"}:
        errors.append("latest baseline scheduled-checkout status is malformed")
    if scheduler.get("status") == "VERIFIED" and not scheduler.get("external_scheduler_checkout_sha"):
        errors.append("verified scheduled checkout requires its deployed commit SHA")
    if ci.get("head_sha") != checkout.get("commit") or ci.get("run_id") is None:
        errors.append("latest CI run is not tied to the captured source commit")
    if ci.get("status_at_capture") not in {"IN_PROGRESS", "COMPLETED", "UNAVAILABLE"}:
        errors.append("latest CI status is malformed")
    required_jobs = {"test (ubuntu-24.04)", "test (windows-latest)",
                     "reproducibility (ubuntu-24.04)", "reproducibility (windows-2022)"}
    if set(ci.get("jobs_at_capture", {})) != required_jobs:
        errors.append("latest baseline does not capture all required CI jobs")
    if type(approvals.get("current_pr_reviews")) is not int:
        errors.append("latest owner-approval review count is malformed")
    return errors


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
    try:
        latest = json.loads(LATEST_BASELINE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"latest dated baseline cannot be read: {exc}")
    else:
        errors.extend(validate_latest_snapshot(latest))
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
