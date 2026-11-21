"""Execution modes (FE-0005, FE-0006) - fail-closed by construction.

* Five modes exist: ``RESEARCH_REPLAY``, ``SHADOW``, ``LIVE_PAPER``, ``BROKER_DRYRUN``, ``LIVE_CASH``.
* A mode is granted only by :func:`resolve_mode`. Nothing else in the codebase may construct a session in a higher
  mode than it was granted; sessions are frozen dataclasses with no setter.
* ``PHYSICAL_ORDERS_ENABLED`` is a build-policy constant set to ``False``. The new pipeline has no path to grant
  ``LIVE_CASH``; credentials, approvals, environment variables and UI requests cannot override this policy.
* Shadow and replay modes never emit fills; only ``LIVE_PAPER`` emits paper fills.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from collections.abc import Sequence

from financial_brain.forward_edge.approvals import Approval
from financial_brain.execution.order_policy import (
    PHYSICAL_ORDERS_ENABLED as PHYSICAL_ORDERS_ENABLED,
    PhysicalOrderDenied,
    deny_physical_order,
)

class Mode(str, Enum):
    RESEARCH_REPLAY = "RESEARCH_REPLAY"
    SHADOW = "SHADOW"
    LIVE_PAPER = "LIVE_PAPER"
    BROKER_DRYRUN = "BROKER_DRYRUN"
    LIVE_CASH = "LIVE_CASH"


class ModeError(PermissionError):
    pass


@dataclass(frozen=True)
class ComplianceRecord:
    """Broker/exchange compliance evidence (FE-0133, FE-0134). ``verified`` must come from a reviewed record."""

    broker_id: str
    verified: bool
    checked_at_utc: datetime | None
    evidence_sha256: str | None


@dataclass(frozen=True)
class SessionMode:
    """The mode a session was granted. Immutable: a higher mode needs a new session and a new grant."""

    mode: Mode

    @property
    def may_emit_paper_fill(self) -> bool:
        return self.mode is Mode.LIVE_PAPER

    @property
    def may_send_physical_order(self) -> bool:
        return False  # no code path in this repository sends a cash order; see module docstring


def resolve_mode(
    requested: Mode,
    *,
    now: datetime,
    approvals: Sequence[Approval] = (),
    compliance: ComplianceRecord | None = None,
    live_cash_scope_sha256: str | None = None,
    live_cash_subject: str | None = None,
) -> SessionMode:
    """Grant ``requested`` or raise :class:`ModeError`. Every higher-risk mode fails closed."""
    if not isinstance(requested, Mode):
        raise ModeError("mode must be a Mode member")
    if requested is Mode.LIVE_CASH:
        try:
            deny_physical_order("LIVE_CASH")
        except PhysicalOrderDenied as exc:
            raise ModeError(f"LIVE_CASH unavailable: {exc}") from exc
    if requested is Mode.BROKER_DRYRUN and (compliance is None or not compliance.verified):
        raise ModeError("BROKER_DRYRUN needs a verified broker compliance record")
    return SessionMode(requested)
