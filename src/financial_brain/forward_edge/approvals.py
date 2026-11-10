"""Owner approvals (FE-0008, FE-0129, FE-0142) - humans only, scoped, expiring.

An approval binds a human actor to one subject and one exact scope (a hash of the charter, limits or instrument
list being approved). It is rejected when the actor is not a human, when it has expired, when it is not yet valid,
or when the scope hash no longer matches (a stale approval for an older charter).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

APPROVAL_KINDS = frozenset({
    "STRATEGY_ENROLLMENT", "ACCOUNT", "VENUE", "LEVERAGE", "CAPITAL", "LIVE_CASH_ENABLE",
    "PROTECTED_TRIAL", "TRIAL_BUDGET_AMENDMENT", "MICRO_RISK_BUDGET", "DISARM_RESET",
})
#: Actor identifiers that name software, not a person. Matched case-insensitively at the start of the id.
_AGENT_ID = re.compile(r"^(agent|ai|llm|bot|claude|gpt|assistant|system|automation|cron|ci|script|model)([:/_\- ]|$)",
                       re.IGNORECASE)
_SHA = re.compile(r"^[0-9a-f]{64}$")


class ApprovalError(ValueError):
    pass


@dataclass(frozen=True)
class Approval:
    kind: str
    subject: str
    scope_sha256: str
    actor_id: str
    actor_type: str
    approved_at_utc: datetime
    expires_at_utc: datetime
    adr_ref: str | None = None


def is_agent_actor(actor_id: str, actor_type: str) -> bool:
    return actor_type != "human" or bool(_AGENT_ID.match(actor_id.strip()))


def validate_approval(approval: Approval, *, kind: str, subject: str, scope_sha256: str, now: datetime) -> None:
    """Raise :class:`ApprovalError` unless ``approval`` is a valid human approval for exactly this scope."""
    if kind not in APPROVAL_KINDS:
        raise ApprovalError(f"unknown approval kind {kind}")
    if approval.kind != kind:
        raise ApprovalError(f"approval is for {approval.kind}, not {kind}")
    if is_agent_actor(approval.actor_id, approval.actor_type):
        raise ApprovalError("agent or non-human actors cannot approve")
    if not approval.actor_id.strip():
        raise ApprovalError("approval has no named human")
    if not _SHA.match(scope_sha256) or not _SHA.match(approval.scope_sha256):
        raise ApprovalError("scope hash must be sha256 hex")
    if approval.subject != subject:
        raise ApprovalError("approval names a different subject")
    if approval.scope_sha256 != scope_sha256:
        raise ApprovalError("stale approval: scope hash changed since approval")
    for stamp in (now, approval.approved_at_utc, approval.expires_at_utc):
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ApprovalError("approval and clock timestamps must be timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    if now_utc < approval.approved_at_utc.astimezone(timezone.utc):
        raise ApprovalError("approval is not yet valid")
    if now_utc >= approval.expires_at_utc.astimezone(timezone.utc):
        raise ApprovalError("approval has expired")
