"""Verify the PR #40/main public-contract table against frozen Git objects."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs/edge-program/pr40-main-contract-reconciliation-2026-11-23.md"
CONTRACTS = ROOT / "docs/edge-program/authoritative-contracts.v1.json"
PR40 = "39817e98dea70640c26d7530dcf51a23d0cce2bb"
MAIN = "3c99b5dddc775db888822b4cec1849e5ebca56c8"
EXPECTED_ACTIONS = {
    "operator_cli": "KEEP",
    "prospective_session_lifecycle": "MERGE",
    "legacy_intraday_paper": "KEEP",
    "broker_oms": "KEEP",
    "simulated_forward_execution": "MERGE",
    "risk_policy_and_sizing": "KEEP",
    "strategy_identity_and_lifecycle": "KEEP",
    "prospective_promotion_evidence": "KEEP",
    "forecast_path_distribution": "KEEP",
    "canonical_portfolio_accounting": "KEEP",
}


def _blob(revision: str, path: str) -> str:
    return subprocess.check_output(["git", "rev-parse", f"{revision}:{path}"], cwd=ROOT,
                                  text=True, stderr=subprocess.STDOUT).strip()


def validate(report: str, contracts: dict, *, blob_lookup=_blob, exists=Path.exists) -> None:
    rows = [line for line in report.splitlines() if line.startswith("|") and "**" in line]
    found: dict[str, list[str]] = {}
    for row in rows:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        if len(cells) < 5:
            continue
        found[cells[0].strip("`")] = cells
    if set(found) != set(EXPECTED_ACTIONS):
        raise ValueError("reconciliation must contain every canonical public contract exactly once")
    by_id = {item["id"]: item for item in contracts.get("contracts", [])}
    if set(by_id) != set(EXPECTED_ACTIONS):
        raise ValueError("canonical contract registry changed; reconciliation must be refreshed")
    for key, expected_action in EXPECTED_ACTIONS.items():
        cells = found[key]
        if expected_action not in cells[2]:
            raise ValueError(f"{key} has an unexpected reconciliation action")
        path = by_id[key]["owner"].split("::", 1)[0]
        old, new = blob_lookup(PR40, path), blob_lookup(MAIN, path)
        # The row's owner-blob cell carries both abbreviated object IDs.
        code_cell = cells[1]
        recorded = re.findall(r"[0-9a-f]{8,40}", code_cell)
        if recorded != [old[:8], new[:8]]:
            raise ValueError(f"{key} owner blob evidence does not match PR #40 and main")
        test_paths = re.findall(r"`(tests/[^`]+)`", cells[4])
        if not test_paths or any(not exists(ROOT / path) for path in test_paths):
            raise ValueError(f"{key} must cite existing focused tests")
    if "No full contract is classified `REIMPLEMENT` or `REJECT`" not in report:
        raise ValueError("report must explain why no contract is reimplemented or rejected")


def main() -> int:
    try:
        report = REPORT.read_text(encoding="utf-8")
        contracts = json.loads(CONTRACTS.read_text(encoding="utf-8"))
        validate(report, contracts)
    except (OSError, json.JSONDecodeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"FAIL PR #40/main contract reconciliation: {exc}")
        return 1
    print("PR #40/main public-contract table matches the pinned Git blobs and current test paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
