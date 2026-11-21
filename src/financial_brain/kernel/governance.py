"""Roles, tamper-evident audit, secret scrubbing and agent tool tiers (FB-0506, FB-0508, FB-0561, FB-0563, FB-0569, FB-0576, FB-0507, FB-0517).

* Roles are a closed matrix. No role other than ``trader`` with ``submit`` and an OMS path can submit paper orders;
  ``researcher`` is explicitly denied order submission, so research cannot bypass OMS controls.
* The audit log is a hash chain: each record commits to the previous hash. Editing or deleting a record breaks
  verification.
* ``scrub`` removes secret-looking values before text reaches logs or a model context.
* Agent tools carry a tier. The read-only tier cannot invoke any write tool. Untrusted text is treated as data and
  can never change a permission or a number used for sizing.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field

ROLES: dict[str, frozenset[str]] = {
    "observer": frozenset({"read"}),
    "researcher": frozenset({"read", "write_research"}),
    "trader": frozenset({"read", "submit_paper"}),
    "operator": frozenset({"read", "reset_latch_request"}),
    "administrator": frozenset({"read", "write_config"}),
}
FORBIDDEN_FOR_RESEARCH = frozenset({"submit_paper", "submit_live", "reset_latch"})


class AuthzError(PermissionError):
    pass


def authorize(role: str, permission: str) -> None:
    perms = ROLES.get(role)
    if perms is None:
        raise AuthzError(f"unknown role {role!r}; default is no authority")
    if permission in FORBIDDEN_FOR_RESEARCH and role == "researcher":
        raise AuthzError("researchers cannot submit orders or reset latches")
    if permission not in perms:
        raise AuthzError(f"role {role} lacks {permission}")


@dataclass
class AuditLog:
    records: list[dict] = field(default_factory=list)

    def append(self, actor: str, action: str, detail: dict) -> dict:
        if not actor:
            raise AuthzError("audit records need an actor")
        prev = self.records[-1]["hash"] if self.records else "GENESIS"
        body = {"seq": len(self.records), "actor": actor, "action": action, "detail": detail, "prev": prev}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        rec = {**body, "hash": digest}
        self.records.append(rec)
        return rec

    def verify(self) -> bool:
        prev = "GENESIS"
        for i, rec in enumerate(self.records):
            body = {k: v for k, v in rec.items() if k != "hash"}
            if body["seq"] != i or body["prev"] != prev:
                return False
            digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            if digest != rec["hash"]:
                return False
            prev = rec["hash"]
        return True


_SECRET = re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*\S+")
_LONG_KEY = re.compile(r"\b[A-Za-z0-9]{32,}\b")


def scrub(text: str) -> str:
    text = _SECRET.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    return _LONG_KEY.sub("[REDACTED]", text)


@dataclass(frozen=True)
class Tool:
    name: str
    tier: str  # READ or WRITE


def invoke(tool: Tool, *, agent_tier: str) -> None:
    if agent_tier == "READ" and tool.tier == "WRITE":
        raise AuthzError(f"read-only agent cannot invoke write tool {tool.name}")


def accept_numeric_output(value: object, *, low: float, high: float) -> float:
    """Model-proposed sizing must be a finite number inside a fixed range; otherwise it is refused."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("sizing must be a number")
    v = float(value)
    if not math.isfinite(v) or not (low <= v <= high):
        raise ValueError("sizing outside allowed range")
    return v
