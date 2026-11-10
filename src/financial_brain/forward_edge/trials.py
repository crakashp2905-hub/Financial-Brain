"""Protected trial budget and append-only trial ledger (FE-0002, FE-0088, FE-0090).

The 2026Q4 protected budget is 13 trials counted from ADR-0004 (``docs/trials/ledger.jsonl``, rows with
``kind == "trial"``). The budget is spent. A new protected trial is refused unless a human approval of kind
``PROTECTED_TRIAL`` references an ADR; once the budget is exhausted, a ``TRIAL_BUDGET_AMENDMENT`` approval is also
required. Retrospective edits are detected by pinned SHA-256 digests (``docs/forward-edge/protected-pins.json``).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from financial_brain.forward_edge.approvals import Approval, ApprovalError, validate_approval

LEDGER_REL = "docs/trials/ledger.jsonl"
PINS_REL = "docs/forward-edge/protected-pins.json"
Q4_2026 = (date(2026, 10, 1), date(2026, 12, 31))
Q4_2026_BUDGET = 13


class TrialGovernanceError(PermissionError):
    pass


@dataclass(frozen=True)
class BudgetStatus:
    window: tuple[date, date]
    budget: int
    spent: int

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.budget

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.spent)


def sha256_file(path: Path, *, normalize_eol: bool = False) -> str:
    """SHA-256 of a file's bytes. ``normalize_eol`` maps CRLF to LF first, so a checkout that converts line endings
    (Windows autocrlf) hashes the same as the git blob and as a Linux checkout."""
    data = path.read_bytes()
    if normalize_eol:
        data = data.replace(bytes([13, 10]), bytes([10]))
    return hashlib.sha256(data).hexdigest()


def load_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def budget_status(path: Path, window: tuple[date, date] = Q4_2026, budget: int = Q4_2026_BUDGET) -> BudgetStatus:
    spent = 0
    for row in load_rows(path):
        if row.get("kind") != "trial":
            continue
        day = date.fromisoformat(str(row["date"])[:10])
        if window[0] <= day <= window[1]:
            spent += 1
    return BudgetStatus(window=window, budget=budget, spent=spent)


def assert_new_protected_trial_allowed(
    repo_root: Path,
    *,
    proposed_on: date,
    trial_name: str,
    approvals: tuple[Approval, ...] = (),
    now: datetime,
    scope_sha256: str,
) -> BudgetStatus:
    """Raise :class:`TrialGovernanceError` unless a new protected trial has explicit, valid, human authority."""
    if not trial_name.strip():
        raise TrialGovernanceError("a protected trial needs a name")
    status = budget_status(repo_root / LEDGER_REL)
    if status.window[0] <= proposed_on <= status.window[1] and status.exhausted:
        needed = ["TRIAL_BUDGET_AMENDMENT", "PROTECTED_TRIAL"]
    else:
        needed = ["PROTECTED_TRIAL"]
    for kind in needed:
        matches = [a for a in approvals if a.kind == kind]
        if not matches:
            raise TrialGovernanceError(f"new protected trial '{trial_name}' needs a {kind} approval")
        try:
            validate_approval(matches[-1], kind=kind, subject=trial_name, scope_sha256=scope_sha256, now=now)
        except ApprovalError as exc:
            raise TrialGovernanceError(f"{kind} rejected: {exc}") from exc
        if not matches[-1].adr_ref:
            raise TrialGovernanceError(f"{kind} must reference the governing ADR")
    return status


def verify_pins(repo_root: Path) -> list[str]:
    """Return mismatches between pinned protected files and their bytes (empty when all match)."""
    pins = json.loads((repo_root / PINS_REL).read_text(encoding="utf-8"))
    normalized = set(pins.get("normalize_eol", []))
    problems = []
    for rel, expected in sorted(pins["files"].items()):
        path = repo_root / rel
        if not path.exists():
            problems.append(f"missing: {rel}")
        elif sha256_file(path, normalize_eol=rel in normalized) != expected:
            problems.append(f"changed: {rel}")
    return problems
