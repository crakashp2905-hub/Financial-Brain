"""Durable MF investor choices that are explicitly separate from paper orders."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


class MFActionDecisionError(ValueError):
    """Invalid or conflicting investor decision record."""


class MFAction(StrEnum):
    SUBSCRIBE = "SUBSCRIBE"
    REDEEM = "REDEEM"
    SWITCH = "SWITCH"
    SIP = "SIP"
    HOLD = "HOLD"


def _positive_decimal(value: Decimal | str | int | None, label: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise MFActionDecisionError(f"{label} must be a positive finite decimal")
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MFActionDecisionError(f"{label} must be a positive finite decimal") from exc
    if not number.is_finite() or number <= 0:
        raise MFActionDecisionError(f"{label} must be a positive finite decimal")
    return number


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MFActionDecisionError(f"{label} must be timezone aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class MFActionDecision:
    decision_id: str
    account_id: str
    actor_id: str
    action: MFAction
    reason: str
    source_snapshot_sha256: str
    decided_at: datetime
    expires_at: datetime
    scheme_code: str | None = None
    plan: str | None = None
    option: str | None = None
    folio_id: str | None = None
    amount_inr: Decimal | str | int | None = None
    units: Decimal | str | int | None = None
    target_scheme_code: str | None = None
    target_plan: str | None = None
    target_option: str | None = None
    sip_frequency: str | None = None
    sip_start: date | None = None
    sip_end: date | None = None

    def __post_init__(self) -> None:
        for name in ("decision_id", "account_id", "actor_id", "reason"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or value.strip() != value:
                raise MFActionDecisionError(f"{name} is required and must be trimmed")
        try:
            action = MFAction(self.action)
        except (TypeError, ValueError) as exc:
            raise MFActionDecisionError("action must be SUBSCRIBE, REDEEM, SWITCH, SIP, or HOLD") from exc
        object.__setattr__(self, "action", action)
        if (not isinstance(self.source_snapshot_sha256, str) or len(self.source_snapshot_sha256) != 64 or
                any(char not in "0123456789abcdef" for char in self.source_snapshot_sha256)):
            raise MFActionDecisionError("source snapshot needs lowercase SHA-256")
        for name in ("scheme_code", "plan", "option", "folio_id", "target_scheme_code",
                     "target_plan", "target_option", "sip_frequency"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip() or value.strip() != value):
                raise MFActionDecisionError(f"{name} must be a trimmed non-empty string when supplied")
        decided = _utc(self.decided_at, "decided_at")
        expires = _utc(self.expires_at, "expires_at")
        if expires <= decided or expires - decided > timedelta(days=90):
            raise MFActionDecisionError("decision expiry must follow decision time and be within 90 days")
        object.__setattr__(self, "decided_at", decided)
        object.__setattr__(self, "expires_at", expires)
        amount = _positive_decimal(self.amount_inr, "amount_inr")
        units = _positive_decimal(self.units, "units")
        object.__setattr__(self, "amount_inr", amount)
        object.__setattr__(self, "units", units)
        source_fields = (self.scheme_code, self.plan, self.option)
        if action is MFAction.SUBSCRIBE:
            if not all(source_fields) or amount is None or units is not None:
                raise MFActionDecisionError("SUBSCRIBE requires scheme, plan, option and amount INR only")
        elif action is MFAction.REDEEM:
            if not all(source_fields) or not self.folio_id or (amount is None) == (units is None):
                raise MFActionDecisionError("REDEEM requires folio, scheme identity and exactly one of amount or units")
        elif action is MFAction.SWITCH:
            if (not all(source_fields) or not self.folio_id
                    or not all((self.target_scheme_code, self.target_plan, self.target_option))
                    or (amount is None) == (units is None)):
                raise MFActionDecisionError("SWITCH requires source/target identities, folio and exactly one of amount or units")
        elif action is MFAction.SIP:
            if (not all(source_fields) or amount is None or units is not None
                    or self.sip_frequency not in {"WEEKLY", "FORTNIGHTLY", "MONTHLY"}
                    or self.sip_start is None or self.sip_end is None or self.sip_end < self.sip_start):
                raise MFActionDecisionError("SIP requires scheme identity, amount, frequency, and ordered schedule dates")
        elif action is MFAction.HOLD and (amount is not None or units is not None):
            raise MFActionDecisionError("HOLD cannot carry transaction amount or units")

    def body(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "account_id": self.account_id,
            "actor_id": self.actor_id,
            "action": self.action.value,
            "reason": self.reason,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "decided_at": self.decided_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "scheme_code": self.scheme_code,
            "plan": self.plan,
            "option": self.option,
            "folio_id": self.folio_id,
            "amount_inr": str(self.amount_inr) if self.amount_inr is not None else None,
            "units": str(self.units) if self.units is not None else None,
            "target_scheme_code": self.target_scheme_code,
            "target_plan": self.target_plan,
            "target_option": self.target_option,
            "sip_frequency": self.sip_frequency,
            "sip_start": self.sip_start.isoformat() if self.sip_start else None,
            "sip_end": self.sip_end.isoformat() if self.sip_end else None,
            "execution_scope": "PAPER_DECISION_ONLY",
            "physical_orders_authorized": False,
        }

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.body(), sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode("utf-8")).hexdigest()


def record_decision(con, decision: MFActionDecision, *, recorded_at: datetime) -> bool:
    """Append an immutable/idempotent choice; this function cannot create an order."""
    recorded = _utc(recorded_at, "recorded_at")
    if recorded < decision.decided_at:
        raise MFActionDecisionError("decision cannot be recorded before decided_at")
    digest = decision.digest()
    body = json.dumps(decision.body(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    con.execute("BEGIN TRANSACTION")
    try:
        if con.execute("SELECT 1 FROM mf_paper_accounts WHERE account_id=?",
                        [decision.account_id]).fetchone() is None:
            raise MFActionDecisionError("decision must reference an existing local paper account")
        prior = con.execute(
            "SELECT decision_sha256 FROM mf_action_decisions WHERE decision_id=?",
            [decision.decision_id],
        ).fetchone()
        if prior is not None:
            if prior[0] != digest:
                raise MFActionDecisionError("decision ID already exists with different content")
            con.execute("COMMIT")
            return False
        con.execute("""INSERT INTO mf_action_decisions (
            decision_id, account_id, actor_id, action, reason, source_snapshot_sha256,
            decided_at, expires_at, scheme_code, plan, option, folio_id,
            amount_inr, units, target_scheme_code, target_plan, target_option,
            sip_frequency, sip_start, sip_end, execution_scope,
            physical_orders_authorized, decision_sha256, body_json, recorded_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'PAPER_DECISION_ONLY', FALSE, ?, ?, ?)""",
            [decision.decision_id, decision.account_id, decision.actor_id, decision.action.value,
             decision.reason, decision.source_snapshot_sha256, decision.decided_at, decision.expires_at,
             decision.scheme_code, decision.plan, decision.option, decision.folio_id,
             decision.amount_inr, decision.units, decision.target_scheme_code,
             decision.target_plan, decision.target_option, decision.sip_frequency,
             decision.sip_start, decision.sip_end, digest, body, recorded])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return True


def decisions_as_of(con, *, account_id: str, known_at: datetime) -> list[dict[str, Any]]:
    cutoff = _utc(known_at, "known_at")
    rows = con.execute("""SELECT decision_id, action, reason, decided_at, expires_at,
        scheme_code, plan, option, folio_id, amount_inr, units,
        target_scheme_code, target_plan, target_option, sip_frequency, sip_start,
        sip_end, decision_sha256, body_json
        FROM mf_action_decisions WHERE account_id=? AND recorded_at<=? AND decided_at<=?
        ORDER BY decided_at DESC, decision_id LIMIT 50""", [account_id, cutoff, cutoff]).fetchall()
    names = ("decision_id", "action", "reason", "decided_at", "expires_at", "scheme_code",
             "plan", "option", "folio_id", "amount_inr", "units", "target_scheme_code",
             "target_plan", "target_option", "sip_frequency", "sip_start", "sip_end",
             "decision_sha256", "body_json")
    result = []
    for row in rows:
        item = dict(zip(names, row, strict=True))
        item["status"] = "EXPIRED" if item["expires_at"] <= cutoff else "CURRENT"
        if not item["body_json"] or json.loads(item["body_json"]).get("physical_orders_authorized") is not False:
            raise MFActionDecisionError("stored MF decision crossed the paper-decision-only boundary")
        result.append(item)
    return result


__all__ = ["MFAction", "MFActionDecision", "MFActionDecisionError", "decisions_as_of", "record_decision"]
