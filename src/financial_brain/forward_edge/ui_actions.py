"""Fail-closed policy for UI actions; display and intent creation cannot route orders (FE-0122)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Sequence

from financial_brain.forward_edge.approvals import Approval, ApprovalError, validate_approval


class UIAction(StrEnum):
    VIEW = "VIEW"
    EXPORT = "EXPORT"
    ANALYZE = "ANALYZE"
    STAGE_PAPER = "STAGE_PAPER"
    STAGE_LIVE = "STAGE_LIVE"
    SUBMIT_ORDER = "SUBMIT_ORDER"
    CANCEL_ORDER = "CANCEL_ORDER"


READ_ONLY_ACTIONS = frozenset({UIAction.VIEW, UIAction.EXPORT, UIAction.ANALYZE})


@dataclass(frozen=True)
class UIActionAuthorization:
    action: UIAction
    allowed: bool
    read_only: bool
    reason: str


def authorize_ui_action(action: UIAction, *, now: datetime,
                        approvals: Sequence[Approval] = (), subject: str = "",
                        scope_sha256: str = "") -> UIActionAuthorization:
    """Authorize only inert UI actions or explicitly approved staging; never submit/cancel orders."""
    if action in READ_ONLY_ACTIONS:
        return UIActionAuthorization(action, True, True, "READ_ONLY_ACTION")
    if action in {UIAction.SUBMIT_ORDER, UIAction.CANCEL_ORDER}:
        return UIActionAuthorization(action, False, True,
                                     "UI_ORDER_ROUTING_DISABLED_BY_BUILD_POLICY")
    if action not in {UIAction.STAGE_PAPER, UIAction.STAGE_LIVE}:
        return UIActionAuthorization(action, False, True, "UNKNOWN_UI_ACTION")
    kind = "STRATEGY_ENROLLMENT" if action is UIAction.STAGE_PAPER else "LIVE_CASH_ENABLE"
    matching = [approval for approval in approvals if approval.kind == kind]
    if not matching:
        return UIActionAuthorization(action, False, True, f"HUMAN_{kind}_APPROVAL_REQUIRED")
    try:
        validate_approval(matching[-1], kind=kind, subject=subject,
                          scope_sha256=scope_sha256, now=now)
    except ApprovalError as exc:
        return UIActionAuthorization(action, False, True, f"APPROVAL_REJECTED:{exc}")
    return UIActionAuthorization(action, True, True, f"STAGING_APPROVED:{kind}")
