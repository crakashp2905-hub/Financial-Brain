"""Balanced, append-only journal for the MF paper account cash lifecycle."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from ..forward_edge.data_permissions import DataPermission, DataUse
from ..kernel.trading_clock import IST


class MFPaperLedgerError(ValueError):
    """The MF journal is unbalanced, conflicting, or lacks evidence."""


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MFPaperLedgerError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def post_journal(
    con,
    *,
    account_id: str,
    transaction_id: str,
    effective_at: datetime,
    recorded_at: datetime,
    entries: tuple[tuple[str, Decimal | str | int, Decimal | str | int], ...],
    basis: str = "SIMULATED_ASSUMPTION",
    source_id: str | None = None,
    source_ref: str | None = None,
    source_sha256: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Post a balanced journal atomically and idempotently inside caller transaction."""
    effective = _aware(effective_at, "effective_at")
    recorded = _aware(recorded_at, "recorded_at")
    if recorded < effective:
        raise MFPaperLedgerError("journal cannot be recorded before effective time")
    if basis not in {"SIMULATED_ASSUMPTION", "OBSERVED_EVIDENCE"}:
        raise MFPaperLedgerError("unknown journal evidence basis")
    if basis == "OBSERVED_EVIDENCE":
        if not all((source_id, source_ref, source_sha256)):
            raise MFPaperLedgerError("observed journal requires source identity, reference and hash")
        assert source_sha256 is not None
        if not re.fullmatch(r"[0-9a-fA-F]{64}", source_sha256 or ""):
            raise MFPaperLedgerError("source hash must be SHA-256")
        source_sha256 = source_sha256.lower()
    elif any((source_id, source_ref, source_sha256)):
        raise MFPaperLedgerError("simulated journal cannot claim source evidence")
    if not account_id.strip() or not transaction_id.strip() or not entries:
        raise MFPaperLedgerError("account, transaction and journal entries are required")
    normalized = []
    for code, debit_raw, credit_raw in entries:
        debit, credit = Decimal(str(debit_raw)), Decimal(str(credit_raw))
        if (not debit.is_finite() or not credit.is_finite() or debit < 0 or credit < 0
                or (debit == 0) == (credit == 0)):
            raise MFPaperLedgerError("each journal line must have exactly one positive side")
        normalized.append((code, debit, credit))
    debit_total = sum((line[1] for line in normalized), Decimal(0))
    credit_total = sum((line[2] for line in normalized), Decimal(0))
    if debit_total != credit_total:
        raise MFPaperLedgerError("journal debits and credits do not balance")
    payload = {
        "account_id": account_id, "transaction_id": transaction_id,
        "effective_at": effective.isoformat(), "basis": basis,
        "source_id": source_id, "source_ref": source_ref,
        "source_sha256": source_sha256,
        "entries": [(code, str(debit), str(credit)) for code, debit, credit in normalized],
        "evidence": evidence or {},
    }
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    existing = con.execute("""SELECT COUNT(*), MIN(event_sha256) FROM mf_paper_ledger_entries
        WHERE account_id=? AND transaction_id=?""", [account_id, transaction_id]).fetchone()
    if existing[0]:
        if existing[0] != len(normalized) or existing[1] != digest:
            raise MFPaperLedgerError("transaction ID was reused for different journal evidence")
        return {"account_id": account_id, "transaction_id": transaction_id,
                "debits_inr": debit_total, "credits_inr": credit_total,
                "event_sha256": digest, "status": "already_posted"}
    evidence_json = _canonical(evidence or {})
    for line_no, (code, debit, credit) in enumerate(normalized, 1):
        con.execute("""INSERT INTO mf_paper_ledger_entries (
            account_id, transaction_id, line_no, effective_at, recorded_at,
            account_code, debit_inr, credit_inr, basis, source_id, source_ref,
            source_sha256, event_sha256, evidence_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [account_id, transaction_id, line_no, effective, recorded, code,
             debit, credit, basis, source_id, source_ref, source_sha256,
             digest, evidence_json])
    return {"account_id": account_id, "transaction_id": transaction_id,
            "debits_inr": debit_total, "credits_inr": credit_total,
            "event_sha256": digest, "status": "posted"}


def record_external_cash_flow(
    con,
    *,
    account_id: str,
    event_id: str,
    amount_inr: Decimal | str | int,
    direction: str,
    effective_at: datetime,
    recorded_at: datetime,
    basis: str,
    source_id: str | None = None,
    source_ref: str | None = None,
    source_sha256: str | None = None,
    permissions: tuple[DataPermission, ...] = (),
) -> dict[str, Any]:
    """Record an owner contribution/withdrawal with cash impact and balanced journal."""
    amount = Decimal(str(amount_inr))
    if not amount.is_finite() or amount <= 0:
        raise MFPaperLedgerError("external cash amount must be positive and finite")
    if direction not in {"CONTRIBUTION", "WITHDRAWAL"}:
        raise MFPaperLedgerError("direction must be CONTRIBUTION or WITHDRAWAL")
    event_time = _aware(effective_at, "effective_at")
    recorded_time = _aware(recorded_at, "recorded_at")
    if recorded_time < event_time:
        raise MFPaperLedgerError("cash event cannot be recorded before effective time")
    if basis == "OBSERVED_EVIDENCE":
        event_date = event_time.astimezone(IST).date()
        allowed = [permission for permission in permissions if permission.allows(
            source_id=source_id or "", use=DataUse.PAPER_SIMULATION, on=event_date,
        )]
        if not allowed:
            raise MFPaperLedgerError("observed cash source lacks PAPER_SIMULATION permission")
    con.execute("BEGIN TRANSACTION")
    try:
        account = con.execute("SELECT cash_inr FROM mf_paper_accounts WHERE account_id=?",
                              [account_id]).fetchone()
        if account is None:
            raise MFPaperLedgerError("MF paper account does not exist")
        old = con.execute("""SELECT event_sha256, amount_inr, direction FROM
            mf_paper_external_cash_events WHERE account_id=? AND event_id=?""",
            [account_id, event_id]).fetchone()
        permission_evidence = sorted((
            item.source_id, item.use.value, item.status.value,
            item.effective_from, item.effective_through,
            item.evidence_ref, item.reviewer,
        ) for item in allowed) if basis == "OBSERVED_EVIDENCE" else []
        payload = {"account_id": account_id, "event_id": event_id,
                   "amount_inr": str(amount), "direction": direction,
                   "effective_at": event_time.isoformat(),
                   "basis": basis, "source_id": source_id, "source_ref": source_ref,
                   "source_sha256": source_sha256,
                   "permission_evidence": permission_evidence}
        digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
        if old is not None:
            if old[0] != digest:
                raise MFPaperLedgerError("external cash event ID reused with different evidence")
            con.execute("COMMIT")
            return {**payload, "status": "already_recorded"}
        cash = Decimal(account[0])
        if direction == "WITHDRAWAL" and amount > cash:
            raise MFPaperLedgerError("withdrawal exceeds available cash")
        delta = amount if direction == "CONTRIBUTION" else -amount
        con.execute("UPDATE mf_paper_accounts SET cash_inr=cash_inr+? WHERE account_id=?",
                    [delta, account_id])
        if direction == "CONTRIBUTION":
            lines = (("CASH", amount, 0), ("OWNER_EQUITY", 0, amount))
        else:
            lines = (("OWNER_WITHDRAWALS", amount, 0), ("CASH", 0, amount))
        post_journal(
            con, account_id=account_id, transaction_id=f"external:{event_id}",
            effective_at=effective_at, recorded_at=recorded_at, entries=lines,
            basis=basis, source_id=source_id, source_ref=source_ref,
            source_sha256=source_sha256, evidence={"event_id": event_id,
            "direction": direction, "amount_inr": str(amount),
            "permission_evidence": permission_evidence},
        )
        con.execute("""INSERT INTO mf_paper_external_cash_events (
            account_id, event_id, amount_inr, direction, effective_at, recorded_at,
            basis, source_id, source_ref, source_sha256, event_sha256)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [account_id, event_id, amount, direction, _aware(effective_at, "effective_at"),
             recorded_time, basis, source_id, source_ref,
             source_sha256, digest])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {**payload, "status": "recorded", "cash_inr": cash + delta,
            "event_sha256": digest}


def ledger_snapshot(con, *, account_id: str) -> dict[str, Any]:
    """Reconcile journal cash to the account's canonical cash balance."""
    account = con.execute("SELECT cash_inr FROM mf_paper_accounts WHERE account_id=?",
                          [account_id]).fetchone()
    if account is None:
        raise MFPaperLedgerError("MF paper account does not exist")
    rows = con.execute("""SELECT account_code,
        COALESCE(SUM(debit_inr-credit_inr),0) FROM mf_paper_ledger_entries
        WHERE account_id=? GROUP BY account_code""", [account_id]).fetchall()
    totals = con.execute("""SELECT COALESCE(SUM(debit_inr),0),
        COALESCE(SUM(credit_inr),0) FROM mf_paper_ledger_entries WHERE account_id=?""",
        [account_id]).fetchone()
    balances = {code: Decimal(value) for code, value in rows}
    journal_cash = balances.get("CASH", Decimal(0))
    cash = Decimal(account[0])
    return {"account_id": account_id, "cash_inr": cash,
            "journal_cash_inr": journal_cash,
            "cash_difference_inr": cash - journal_cash,
            "is_reconciled": cash == journal_cash,
            "journal_debits_inr": Decimal(totals[0]),
            "journal_credits_inr": Decimal(totals[1]),
            "journal_balanced": Decimal(totals[0]) == Decimal(totals[1]),
            "balances_inr": balances,
            "journal_sha256": hashlib.sha256(_canonical([
                (row[0], str(row[1])) for row in sorted(rows)
            ]).encode("utf-8")).hexdigest()}


__all__ = ["MFPaperLedgerError", "ledger_snapshot", "post_journal",
           "record_external_cash_flow"]
