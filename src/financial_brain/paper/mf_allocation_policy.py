"""Owner-configured MF/SIP paper preferences, independent of accounts and orders."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


class MFAllocationPolicyError(ValueError):
    """Invalid, conflicting, or unavailable owner MF policy."""


def _fraction(value: Decimal | str | int, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MFAllocationPolicyError(f"{name} must be a finite decimal fraction") from exc
    if not result.is_finite() or not Decimal(0) <= result <= Decimal(1):
        raise MFAllocationPolicyError(f"{name} must be between zero and one")
    return result


@dataclass(frozen=True)
class MFOwnerAllocationPolicy:
    """One immutable version of a user's local SIP/allocation preferences.

    There is intentionally no broker account, folio ID, credential, or transaction
    permission in this policy. ``owner_label`` is self-declared by the local CLI.
    """

    policy_id: str
    owner_label: str
    version: int
    effective_from: date
    effective_to: date | None
    cash_reserve_fraction: Decimal
    max_equity_fraction: Decimal
    max_debt_fraction: Decimal
    max_etf_fraction: Decimal
    rebalance_band_fraction: Decimal
    sip_amount_inr: Decimal | None = None
    sip_frequency: str | None = None
    sip_day: int | None = None

    def __post_init__(self) -> None:
        if not self.policy_id.strip() or not self.owner_label.strip():
            raise MFAllocationPolicyError("policy_id and owner_label are required")
        if not isinstance(self.version, int) or self.version < 1:
            raise MFAllocationPolicyError("policy version must be a positive integer")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise MFAllocationPolicyError("effective_to precedes effective_from")
        for name in ("cash_reserve_fraction", "max_equity_fraction", "max_debt_fraction",
                     "max_etf_fraction", "rebalance_band_fraction"):
            object.__setattr__(self, name, _fraction(getattr(self, name), name))
        if (self.sip_amount_inr is None) != (self.sip_frequency is None):
            raise MFAllocationPolicyError("SIP amount and frequency must be set together")
        if self.sip_amount_inr is not None:
            try:
                amount = Decimal(str(self.sip_amount_inr))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise MFAllocationPolicyError("SIP amount must be a finite positive decimal") from exc
            if not amount.is_finite() or amount <= 0:
                raise MFAllocationPolicyError("SIP amount must be a finite positive decimal")
            object.__setattr__(self, "sip_amount_inr", amount)
            frequency = str(self.sip_frequency).upper()
            if frequency not in {"WEEKLY", "FORTNIGHTLY", "MONTHLY"}:
                raise MFAllocationPolicyError("unsupported SIP frequency")
            object.__setattr__(self, "sip_frequency", frequency)
            if not isinstance(self.sip_day, int) or not 1 <= self.sip_day <= 31:
                raise MFAllocationPolicyError("SIP day must be between 1 and 31")
        elif self.sip_day is not None:
            raise MFAllocationPolicyError("SIP day requires a SIP amount and frequency")

    def body(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id, "owner_label": self.owner_label,
            "version": self.version, "effective_from": self.effective_from.isoformat(),
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "cash_reserve_fraction": str(self.cash_reserve_fraction),
            "max_equity_fraction": str(self.max_equity_fraction),
            "max_debt_fraction": str(self.max_debt_fraction),
            "max_etf_fraction": str(self.max_etf_fraction),
            "rebalance_band_fraction": str(self.rebalance_band_fraction),
            "sip_amount_inr": str(self.sip_amount_inr) if self.sip_amount_inr is not None else None,
            "sip_frequency": self.sip_frequency, "sip_day": self.sip_day,
            "mode": "PAPER_DECISION_ONLY", "external_fund_transactions": False,
        }

    def digest(self) -> str:
        payload = json.dumps(self.body(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_policy(con, policy: MFOwnerAllocationPolicy, *, recorded_at: datetime) -> dict[str, Any]:
    if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
        raise MFAllocationPolicyError("recorded_at must be timezone-aware")
    received = recorded_at.astimezone(timezone.utc)
    digest = policy.digest()
    body = policy.body()
    con.execute("BEGIN TRANSACTION")
    try:
        old = con.execute("""SELECT payload_sha256 FROM mf_owner_allocation_policies
            WHERE policy_id=? AND version=?""", [policy.policy_id, policy.version]).fetchone()
        if old is not None:
            if old[0] != digest:
                raise MFAllocationPolicyError("policy ID/version already exists with different settings")
            con.execute("COMMIT")
            return {"policy_id": policy.policy_id, "version": policy.version,
                    "policy_sha256": digest, "status": "already_recorded",
                    "external_fund_transactions": False}
        overlaps = con.execute("""SELECT version FROM mf_owner_allocation_policies
            WHERE policy_id=? AND NOT (COALESCE(effective_to, DATE '9999-12-31') < ?
                OR COALESCE(?, DATE '9999-12-31') < effective_from)""",
            [policy.policy_id, policy.effective_from, policy.effective_to]).fetchall()
        if overlaps:
            raise MFAllocationPolicyError("effective policy versions may not overlap")
        con.execute("""INSERT INTO mf_owner_allocation_policies (
            policy_id, owner_label, version, effective_from, effective_to,
            cash_reserve_fraction, max_equity_fraction, max_debt_fraction,
            max_etf_fraction, rebalance_band_fraction, sip_amount_inr,
            sip_frequency, sip_day, payload_sha256, payload_json, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", [
                policy.policy_id, policy.owner_label, policy.version,
                policy.effective_from, policy.effective_to,
                policy.cash_reserve_fraction, policy.max_equity_fraction,
                policy.max_debt_fraction, policy.max_etf_fraction,
                policy.rebalance_band_fraction, policy.sip_amount_inr,
                policy.sip_frequency, policy.sip_day, digest,
                json.dumps(body, sort_keys=True, separators=(",", ":")), received,
            ])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"policy_id": policy.policy_id, "version": policy.version,
            "policy_sha256": digest, "status": "recorded",
            "external_fund_transactions": False}


def policy_as_of(con, policy_id: str, *, as_of: date, known_at: datetime) -> dict[str, Any] | None:
    if known_at.tzinfo is None or known_at.utcoffset() is None:
        raise MFAllocationPolicyError("known_at must be timezone-aware")
    row = con.execute("""SELECT payload_json, payload_sha256 FROM mf_owner_allocation_policies
        WHERE policy_id=? AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)
          AND created_at<=? ORDER BY version DESC LIMIT 1""",
        [policy_id, as_of, as_of, known_at.astimezone(timezone.utc)]).fetchone()
    if row is None:
        return None
    body = json.loads(row[0])
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if hashlib.sha256(payload.encode("utf-8")).hexdigest() != row[1]:
        raise MFAllocationPolicyError("persisted owner policy hash does not match its content")
    return {**body, "policy_sha256": row[1]}


__all__ = ["MFAllocationPolicyError", "MFOwnerAllocationPolicy", "policy_as_of", "record_policy"]
