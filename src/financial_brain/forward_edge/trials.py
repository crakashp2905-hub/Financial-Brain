"""Protected trial budget and append-only trial ledger (FE-0002, FE-0088, FE-0090, PE-0013).

The 2026Q4 protected budget is 13 trials counted from ADR-0004 (``docs/trials/ledger.jsonl``, rows with
``kind == "trial"``). The budget is spent. A new protected trial is refused unless a human approval of kind
``PROTECTED_TRIAL`` references an ADR. Budget increases must be recorded in ``trial-program.v1.json`` and separately
approved with a matching ``TRIAL_BUDGET_AMENDMENT``. Q1 2027 is closed until an owner-approved protocol ADR and
explicit budget are recorded; a generic trial approval cannot activate it. Retrospective edits are detected by
pinned SHA-256 digests (``docs/forward-edge/protected-pins.json``).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from financial_brain.forward_edge.approvals import Approval, ApprovalError, validate_approval
from financial_brain.forward_edge.charter import CharterError, ExperimentCharter
from financial_brain.forward_edge.decision_custody import CohortSchedule

LEDGER_REL = "docs/trials/ledger.jsonl"
PINS_REL = "docs/forward-edge/protected-pins.json"
PROGRAM_REL = "docs/forward-edge/trial-program.v1.json"
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


def sha256_file(path: Path) -> str:
    # Git checkouts may materialize CRLF while the committed blob uses LF.
    # Pin the canonical text content so Windows and Linux verify the same record.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


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


def load_programs(repo_root: Path) -> list[dict]:
    """Load non-overlapping, explicit protected-trial windows; null budgets stay unauthorized."""
    document = json.loads((repo_root / PROGRAM_REL).read_text(encoding="utf-8"))
    if document.get("schema") != "fb.forward_edge.trial_program.v1":
        raise TrialGovernanceError("unsupported protected-trial program schema")
    programs = document.get("programs")
    if not isinstance(programs, list) or not programs:
        raise TrialGovernanceError("protected-trial program must define at least one window")
    parsed: list[tuple[date, date, dict]] = []
    ids: set[str] = set()
    supported_ids = {"Q4-2026", "Q1-2027"}
    for program in programs:
        try:
            program_id = str(program["program_id"])
            start, end = date.fromisoformat(program["start_date"]), date.fromisoformat(program["end_date"])
            status, budget = str(program["status"]), program["budget"]
        except (KeyError, TypeError, ValueError) as exc:
            raise TrialGovernanceError("malformed protected-trial program window") from exc
        if program_id not in supported_ids:
            raise TrialGovernanceError(f"trial program needs an explicit code-reviewed policy: {program_id}")
        if program_id in ids or end < start:
            raise TrialGovernanceError("trial program IDs and dates must be unique and ordered")
        ids.add(program_id)
        if status not in {"NOT_AUTHORIZED", "ACTIVE", "EXHAUSTED", "AMENDED"}:
            raise TrialGovernanceError(f"unknown trial program status: {status}")
        if budget is not None and (type(budget) is not int or budget < 1):
            raise TrialGovernanceError("an authorized trial budget must be a positive integer")
        if status == "NOT_AUTHORIZED" and any((
            budget is not None,
            program.get("protocol_adr_ref"),
            program.get("budget_adr_ref"),
            program.get("authorized_owner_id"),
        )):
            raise TrialGovernanceError("NOT_AUTHORIZED program cannot carry an active budget or protocol ADR")
        if status in {"ACTIVE", "EXHAUSTED", "AMENDED"} and (
            budget is None or not program.get("protocol_adr_ref")
        ):
            raise TrialGovernanceError("authorized/exhausted program needs an explicit budget and protocol ADR")
        if status in {"ACTIVE", "AMENDED"} and not str(program.get("authorized_owner_id", "")).strip():
            raise TrialGovernanceError("active program needs an explicitly named approving owner")
        if status == "AMENDED" and not program.get("budget_adr_ref"):
            raise TrialGovernanceError("amended budget must reference its budget ADR")
        if program_id == "Q4-2026" and budget is not None:
            if budget > Q4_2026_BUDGET and status != "AMENDED":
                raise TrialGovernanceError("Q4-2026 budget increase must use AMENDED status and a budget ADR")
            if status == "AMENDED" and budget <= Q4_2026_BUDGET:
                raise TrialGovernanceError("Q4-2026 amendment must increase the protected budget")
        adr_refs = [program.get("protocol_adr_ref")]
        if status == "AMENDED":
            adr_refs.append(program.get("budget_adr_ref"))
        adr_documents: dict[str, Path] = {}
        for adr_ref in filter(None, adr_refs):
            if not re.fullmatch(r"ADR-\d{4}", str(adr_ref)):
                raise TrialGovernanceError("trial program ADR references must use ADR-NNNN")
            prefix = str(adr_ref).split("-")[1] + "-"
            matches = list((repo_root / "docs/adr").glob(f"{prefix}*.md"))
            if len(matches) != 1:
                raise TrialGovernanceError(f"trial program references missing repository ADR {adr_ref}")
            adr_documents[str(adr_ref)] = matches[0]
        if program_id == "Q1-2027" and status != "NOT_AUTHORIZED":
            protocol_text = adr_documents[str(program["protocol_adr_ref"])].read_text(encoding="utf-8").lower()
            if not any(marker in protocol_text for marker in ("q1 2027", "q1-2027")) or "trial" not in protocol_text or "protocol" not in protocol_text:
                raise TrialGovernanceError("Q1-2027 protocol ADR must explicitly describe its Q1 2027 trial protocol")
        parsed.append((start, end, program))
    ordered = sorted(parsed, key=lambda item: item[0])
    if any(ordered[index][1] >= ordered[index + 1][0] for index in range(len(ordered) - 1)):
        raise TrialGovernanceError("protected-trial windows must not overlap")
    return [item[2] for item in ordered]


def protected_trial_scope_sha256(charter: ExperimentCharter, schedule: CohortSchedule) -> str:
    """Hash the exact charter and its cohort schedule into the approval scope."""
    if not isinstance(charter, ExperimentCharter) or not isinstance(schedule, CohortSchedule):
        raise TrialGovernanceError("protected trial requires a typed experiment charter and cohort schedule")
    charter_sha = charter.sha256()
    if schedule.charter_sha256 != charter_sha:
        raise TrialGovernanceError("cohort schedule is bound to a different experiment charter")
    if not schedule.cohorts:
        raise TrialGovernanceError("protected trial cohort schedule cannot be empty")
    # CohortSchedule validates ordering and overlap, but dates and coverage are checked
    # here because this is the policy boundary, not a general-purpose serialization type.
    windows: list[tuple[date, date]] = []
    try:
        for cohort in schedule.cohorts:
            if not cohort.cohort_id.strip():
                raise ValueError("cohort ID is empty")
            start, end = date.fromisoformat(cohort.start), date.fromisoformat(cohort.end)
            if end < start:
                raise ValueError("cohort end precedes its start")
            windows.append((start, end))
        charter_start, charter_end = date.fromisoformat(charter.start_on), date.fromisoformat(charter.end_on)
    except (AttributeError, TypeError, ValueError) as exc:
        raise TrialGovernanceError("charter/cohort schedule contains invalid dates or IDs") from exc
    if any(start < charter_start or end > charter_end for start, end in windows):
        raise TrialGovernanceError("cohort schedule extends outside the charter's frozen dates")
    payload = json.dumps({"charter_sha256": charter_sha, "cohort_schedule_sha256": schedule.sha256()},
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assert_new_protected_trial_allowed(
    repo_root: Path,
    *,
    proposed_on: date,
    trial_name: str,
    approvals: tuple[Approval, ...] = (),
    now: datetime,
    scope_sha256: str,
    charter: ExperimentCharter | None = None,
    cohort_schedule: CohortSchedule | None = None,
) -> BudgetStatus:
    """Raise :class:`TrialGovernanceError` unless a new protected trial has explicit, valid, human authority."""
    if not trial_name.strip():
        raise TrialGovernanceError("a protected trial needs a name")
    rows = load_rows(repo_root / LEDGER_REL)
    if any(row.get("kind") == "trial" and row.get("name") == trial_name for row in rows):
        raise TrialGovernanceError("trial name already exists in the protected ledger")
    program = next((item for item in load_programs(repo_root)
                    if date.fromisoformat(item["start_date"]) <= proposed_on <= date.fromisoformat(item["end_date"])), None)
    if program is None:
        raise TrialGovernanceError("no protected-trial program covers the proposed date")
    program_id = program["program_id"]
    if program["status"] == "NOT_AUTHORIZED" or program["budget"] is None:
        raise TrialGovernanceError(f"{program_id} has no owner-approved protocol and explicit trial budget")
    start, end = date.fromisoformat(program["start_date"]), date.fromisoformat(program["end_date"])
    status = budget_status(repo_root / LEDGER_REL, (start, end), program["budget"])
    needed = ["PROTECTED_TRIAL"]
    if program_id == "Q1-2027":
        needed.insert(0, "Q1_PROTOCOL_AMENDMENT")
    if program["status"] == "AMENDED":
        needed.insert(0, "TRIAL_BUDGET_AMENDMENT")
    if status.exhausted:
        raise TrialGovernanceError(f"{program_id} budget is exhausted; an approved larger explicit budget is required")
    proposed_at_utc = datetime.combine(proposed_on, datetime.min.time(), tzinfo=timezone.utc)
    approval_clock = max(now.astimezone(timezone.utc), proposed_at_utc)
    for kind in needed:
        matches = [a for a in approvals if a.kind == kind]
        if not matches:
            raise TrialGovernanceError(f"new protected trial '{trial_name}' needs a {kind} approval")
        try:
            validate_approval(matches[-1], kind=kind, subject=trial_name, scope_sha256=scope_sha256,
                              now=approval_clock)
        except ApprovalError as exc:
            raise TrialGovernanceError(f"{kind} rejected: {exc}") from exc
        if not matches[-1].adr_ref:
            raise TrialGovernanceError(f"{kind} must reference the governing ADR")
        expected_adr = (program.get("protocol_adr_ref") if kind in {"PROTECTED_TRIAL", "Q1_PROTOCOL_AMENDMENT"}
                        else program.get("budget_adr_ref"))
        if expected_adr and matches[-1].adr_ref != expected_adr:
            raise TrialGovernanceError(f"{kind} must reference the configured ADR {expected_adr}")
        if kind in {"Q1_PROTOCOL_AMENDMENT", "TRIAL_BUDGET_AMENDMENT"}:
            owner_id = str(program.get("authorized_owner_id", "")).strip()
            if not owner_id or matches[-1].actor_id != owner_id:
                raise TrialGovernanceError(f"{kind} must be approved by configured owner {owner_id or '[unset]'}")
    if charter is None or cohort_schedule is None:
        raise TrialGovernanceError("new protected trial needs an exact frozen charter and cohort schedule")
    if charter.charter_id != trial_name:
        raise TrialGovernanceError("charter ID must match the protected trial name")
    if charter.start_on != proposed_on.isoformat():
        raise TrialGovernanceError("charter prospective start must equal the proposed trial date")
    try:
        charter_start, charter_end = date.fromisoformat(charter.start_on), date.fromisoformat(charter.end_on)
        if not charter_start <= proposed_on <= charter_end:
            raise TrialGovernanceError("proposed trial date falls outside the frozen charter")
    except (CharterError, TypeError, ValueError) as exc:
        raise TrialGovernanceError("protected trial charter is invalid") from exc
    expected_scope = protected_trial_scope_sha256(charter, cohort_schedule)
    if not any(date.fromisoformat(cohort.start) <= proposed_on <= date.fromisoformat(cohort.end)
               for cohort in cohort_schedule.cohorts):
        raise TrialGovernanceError("proposed trial date is not covered by the frozen cohort schedule")
    if scope_sha256 != expected_scope:
        raise TrialGovernanceError("approval scope hash must bind the exact charter and cohort schedule")
    return status


def verify_pins(repo_root: Path) -> list[str]:
    """Return mismatches between pinned protected files and their bytes (empty when all match)."""
    pins = json.loads((repo_root / PINS_REL).read_text(encoding="utf-8"))
    if pins.get("hash_newline_normalization") != "CRLF_TO_LF":
        return ["protected pin manifest must declare CRLF_TO_LF hash normalization"]
    problems = []
    for rel, expected in sorted(pins["files"].items()):
        path = repo_root / rel
        if not path.exists():
            problems.append(f"missing: {rel}")
        elif sha256_file(path) != expected:
            problems.append(f"changed: {rel}")
    return problems
