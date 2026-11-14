"""Rehearsal and small-capital rollout tooling (FE-0132 .. FE-0142). Never automatic; the broker adapter is READ_ONLY.

Nothing here opens a network connection or sends an order. ``MockBrokerAdapter`` is an in-memory stand-in for tests; it is
labelled as a mock everywhere and its fills can never be OBSERVED_BROKER evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import json

from financial_brain.forward_edge.approvals import Approval, ApprovalError, validate_approval


class RehearsalError(RuntimeError):
    pass


class OrderBlocked(RehearsalError):
    """Raised by every adapter that is not explicitly staged for orders. There is no staged adapter in this repository."""


# ---- FE-0132 read-only adapter ----

class ReadOnlyBrokerAdapter:
    """Default adapter: it can describe state but cannot send, modify or cancel an order."""

    mode = "READ_ONLY"
    is_mock = False

    def positions(self) -> dict[str, int]:
        raise RehearsalError("no broker configured")

    def place_order(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise OrderBlocked("broker adapter is READ_ONLY: orders are blocked")

    def cancel_order(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise OrderBlocked("broker adapter is READ_ONLY: orders are blocked")


class MockBrokerAdapter(ReadOnlyBrokerAdapter):
    """Deterministic in-memory dry-run responses for rehearsal tests. Never evidence of a real broker."""

    is_mock = True

    def __init__(self, positions: dict[str, int] | None = None) -> None:
        self._positions = dict(positions or {})
        self.dry_run_orders: list[dict] = []

    def positions(self) -> dict[str, int]:
        return dict(self._positions)

    def dry_run_order(self, order_id: str, symbol: str, qty: int) -> dict:
        resp = {"order_id": order_id, "status": "DRY_RUN_ACCEPTED", "mock": True, "filled": 0}
        self.dry_run_orders.append(resp)
        return resp


class KiteReadOnlyStatusAdapter(ReadOnlyBrokerAdapter):
    """Kite account-status reader; delegates only to providers.kite_data's GET allowlist."""

    def account_status(self) -> dict:
        from financial_brain.providers.kite_data import account_status
        return account_status()

    def orders(self) -> list[dict]:
        return list(self.account_status().get("orders", []))

    def positions(self) -> dict:
        return dict(self.account_status().get("positions", {}))


# ---- FE-0134 compliance gate ----

COMPLIANCE_ITEMS = ("broker_permission_for_strategy_class", "registered_static_ip", "authorized_app", "key_rotation_policy",
                    "exchange_algo_identifier_if_required", "owner_confirmation")


def compliance_gate(record: dict[str, bool]) -> tuple[bool, list[str]]:
    """Any missing or false item blocks the stage transition. Evidence for each item is supplied by the owner/broker."""
    missing = [k for k in COMPLIANCE_ITEMS if record.get(k) is not True]
    return (not missing), missing


# ---- FE-0135 rehearsal log ----

@dataclass
class Rehearsal:
    run_id: str
    market_hours: bool
    mock: bool
    unknown_orders: int = 0
    duplicate_orders: int = 0
    ledger_mismatches: int = 0


class RehearsalLog:
    def __init__(self, required_clean_runs: int = 10) -> None:
        self.required = required_clean_runs
        self.clean_streak = 0
        self.resets = 0
        self.runs: list[Rehearsal] = []

    def record(self, run: Rehearsal) -> None:
        self.runs.append(run)
        dirty = run.unknown_orders or run.duplicate_orders or run.ledger_mismatches
        if dirty:
            self.clean_streak = 0
            self.resets += 1
        elif run.market_hours and not run.mock:
            self.clean_streak += 1          # only real market-hours, non-mock runs count toward the gate

    def gate_open(self) -> bool:
        return self.clean_streak >= self.required


# ---- FE-0136 broker-state reconciliation ----

def reconcile_order(local_state: str, broker_status: str | None) -> str:
    """'Placed' is not 'filled': a fill counts only when the broker confirms it."""
    if broker_status is None:
        return "UNKNOWN" if local_state in ("SUBMITTED", "PLACED") else local_state
    if broker_status == "COMPLETE":
        return "FILLED"
    if broker_status in ("OPEN", "TRIGGER PENDING"):
        return "WORKING"
    if broker_status in ("CANCELLED", "REJECTED"):
        return broker_status
    return "UNKNOWN"


# ---- FE-0137 capital-risk proposal ----

@dataclass(frozen=True)
class RiskProposal:
    capital_inr: Decimal
    max_per_order_inr: Decimal
    max_daily_loss_inr: Decimal
    max_margin_inr: Decimal
    emergency_exit_policy_sha256: str

    def __post_init__(self) -> None:
        if (not (0 < self.capital_inr) or not (0 < self.max_per_order_inr <= self.capital_inr)
                or not (0 < self.max_daily_loss_inr <= self.capital_inr)
                or not (0 <= self.max_margin_inr <= self.capital_inr)):
            raise RehearsalError("limits must be positive and within capital")
        if (len(self.emergency_exit_policy_sha256) != 64
                or any(char not in "0123456789abcdef" for char in self.emergency_exit_policy_sha256)):
            raise RehearsalError("emergency exit policy must be bound by a SHA-256 digest")

    def sha256(self) -> str:
        payload = json.dumps({"capital_inr": str(self.capital_inr),
                              "max_per_order_inr": str(self.max_per_order_inr),
                              "max_daily_loss_inr": str(self.max_daily_loss_inr),
                              "max_margin_inr": str(self.max_margin_inr),
                              "emergency_exit_policy_sha256": self.emergency_exit_policy_sha256},
                             sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def proposal_confirmed(proposal: RiskProposal, approval: Approval | None, scope_sha256: str, now: datetime) -> bool:
    """No real trade before a valid human confirmation of this exact proposal."""
    if approval is None:
        return False
    if scope_sha256 != proposal.sha256():
        return False
    try:
        validate_approval(approval, kind="MICRO_RISK_BUDGET", subject="micro_pilot", scope_sha256=scope_sha256, now=now)
    except ApprovalError:
        return False
    return True


# ---- FE-0138 statement reconciliation ----

def reconcile_statement(ledger_rows: list[dict], statement_rows: list[dict]) -> list[str]:
    """Rows are {'ref','qty','amount','fees'}. Any unexplained difference is a mismatch."""
    out = []
    ledger = {r["ref"]: r for r in ledger_rows}
    stmt = {r["ref"]: r for r in statement_rows}
    for ref in sorted(set(ledger) | set(stmt)):
        a, b = ledger.get(ref), stmt.get(ref)
        if a is None:
            out.append(f"{ref}: in statement, not in ledger")
        elif b is None:
            out.append(f"{ref}: in ledger, not in statement")
        else:
            for k in ("qty", "amount", "fees"):
                if a[k] != b[k]:
                    out.append(f"{ref}: {k} differs ({a[k]} vs {b[k]})")
    return out


# ---- FE-0139 / FE-0142 disarm ----

class DisarmLatch:
    TRIGGERS = (
        "LOST_HEARTBEAT", "STALE_BROKER_STATE", "STALE_FEED", "CUSTODY_FAILURE",
        "ABNORMAL_COST", "LEDGER_MISMATCH", "RECONCILIATION_MISMATCH", "RISK_BREACH",
        "UNEXPECTED_EXECUTION",
    )

    def __init__(self) -> None:
        self.armed = True
        self.reason: str | None = None

    def observe(self, trigger: str) -> None:
        if trigger in self.TRIGGERS:
            self.armed = False
            self.reason = trigger

    def rearm(self, approval: Approval | None, *, scope_sha256: str, now: datetime) -> None:
        if approval is None:
            raise RehearsalError("re-arm needs a human actor")
        try:
            validate_approval(approval, kind="DISARM_RESET", subject="disarm_latch", scope_sha256=scope_sha256, now=now)
        except ApprovalError as exc:
            raise RehearsalError(f"re-arm refused: {exc}") from exc
        self.armed, self.reason = True, None


# ---- FE-0140 execution model versions ----

def slippage_report(simulated_bps: list[float], observed_bps: list[float]) -> dict[str, float]:
    if not simulated_bps or len(simulated_bps) != len(observed_bps):
        raise RehearsalError("need aligned simulated and observed slippage")
    diffs = [o - s for s, o in zip(simulated_bps, observed_bps)]
    return {"mean_simulated": sum(simulated_bps) / len(diffs), "mean_observed": sum(observed_bps) / len(diffs),
            "mean_gap": sum(diffs) / len(diffs)}


class ExecutionModelRegistry:
    """Canonical immutable model records addressable by version and content hash."""

    def __init__(self) -> None:
        self._versions: dict[str, tuple[dict, str]] = {}

    def register(self, version: str, params: dict) -> None:
        if version in self._versions:
            raise RehearsalError("version exists; register a new version instead")
        try:
            frozen = json.loads(json.dumps(params, sort_keys=True, separators=(",", ":"), allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise RehearsalError("model parameters must be finite canonical JSON") from exc
        digest = hashlib.sha256(json.dumps(frozen, sort_keys=True, separators=(",", ":"),
                                           allow_nan=False).encode("utf-8")).hexdigest()
        self._versions[version] = (frozen, digest)

    def get(self, version: str) -> dict:
        return json.loads(json.dumps(self._versions[version][0]))

    def sha256(self, version: str) -> str:
        return self._versions[version][1]


# ---- FE-0141 scaling ----

def scaling_allowed(*, profitable_week: bool, separate_review_done: bool, approval_valid: bool) -> bool:
    """A profitable week alone never scales capital: review and explicit approval are both required."""
    return separate_review_done and approval_valid


def scaling_evidence_gate(*, statement_mismatches: list[str], fill_model_error_bps: Decimal,
                          maximum_fill_model_error_bps: Decimal, risk_limits_passed: bool,
                          independent_review_done: bool, owner_approval_valid: bool) -> tuple[bool, list[str]]:
    """Block exposure scaling until observed statement, fill-model, risk, review and approval gates all pass."""
    reasons = []
    if statement_mismatches:
        reasons.append("BROKER_STATEMENT_MISMATCH")
    if (not fill_model_error_bps.is_finite() or not maximum_fill_model_error_bps.is_finite()
            or fill_model_error_bps < 0 or maximum_fill_model_error_bps < 0
            or fill_model_error_bps > maximum_fill_model_error_bps):
        reasons.append("FILL_MODEL_ERROR_OUT_OF_LIMIT")
    if risk_limits_passed is not True:
        reasons.append("RISK_LIMITS_NOT_PASSED")
    if independent_review_done is not True:
        reasons.append("INDEPENDENT_REVIEW_MISSING")
    if owner_approval_valid is not True:
        reasons.append("OWNER_APPROVAL_MISSING")
    return not reasons, reasons
