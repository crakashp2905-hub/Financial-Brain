"""Idempotent import boundary from durable MF paper events into canonical accounting."""
from __future__ import annotations

import re
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from financial_brain.paper.mf_account_ledger import mf_instrument_id

from .canonical_ledger import (
    AssetClass,
    CanonicalPortfolioLedger,
    EventKind,
    PortfolioEvent,
)


class MutualFundImportError(ValueError):
    """An MF paper event cannot be reconciled into canonical accounting."""


_IST = ZoneInfo("Asia/Kolkata")
_HASH = re.compile(r"^[0-9a-f]{64}$")


def _amount(value: object, field: str, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MutualFundImportError(f"{field} must be decimal") from exc
    if not result.is_finite() or (result <= 0 if positive else result < 0):
        raise MutualFundImportError(f"{field} must be {'positive' if positive else 'non-negative'}")
    return result


def _event_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise MutualFundImportError("MF event_date must be an ISO date") from exc


def _recorded_at(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MutualFundImportError("MF created_at must be a timezone-aware timestamp")
    return value


def _require_text(row: Mapping[str, object], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise MutualFundImportError(f"MF event {field} is required")
    return value.strip()


def events_from_mutual_fund_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    account_id: str,
    sleeve_id: str = "MUTUAL_FUND",
    payout_rows: Sequence[Mapping[str, object]] = (),
) -> list[PortfolioEvent]:
    """Translate committed MF lot-book events without inventing NAVs, units or fills.

    The MF event date is the effective NAV date, represented at the end of that IST day.
    ``created_at`` remains in metadata as the local book's record clock. Import is not an
    external verification of the event hash or of an official RTA/broker allotment.
    """
    if not account_id.strip() or not sleeve_id.strip():
        raise MutualFundImportError("canonical account and sleeve are required")
    result: list[PortfolioEvent] = []
    seen_ids: set[str] = set()
    payouts: dict[str, Mapping[str, object]] = {}
    for payout_row in payout_rows:
        redemption_id = _require_text(payout_row, "redemption_event_id")
        if redemption_id in payouts:
            raise MutualFundImportError("MF input contains duplicate payout evidence for one redemption")
        payouts[redemption_id] = payout_row
    for row in rows:
        event_id = _require_text(row, "event_id")
        if event_id in seen_ids:
            raise MutualFundImportError("MF input contains a duplicate event_id")
        seen_ids.add(event_id)
        digest = _require_text(row, "event_sha256")
        if not _HASH.fullmatch(digest):
            raise MutualFundImportError("MF event_sha256 must be a lowercase SHA-256 digest")
        kind = _require_text(row, "kind")
        if kind not in {"SUBSCRIBE", "REDEEM"}:
            raise MutualFundImportError(f"unsupported MF accounting event kind: {kind}")

        scheme = _require_text(row, "scheme_code")
        plan = _require_text(row, "plan")
        option = _require_text(row, "option")
        isin = _require_text(row, "isin")
        nav = _amount(row.get("nav_per_unit"), "nav_per_unit", positive=True)
        units = _amount(row.get("units"), "units", positive=True)
        gross = _amount(row.get("gross_inr"), "gross_inr", positive=True)
        exit_load = _amount(row.get("exit_load_inr", 0), "exit_load_inr")
        other_charges = _amount(row.get("other_charges_inr", 0), "other_charges_inr")
        net = _amount(row.get("net_inr"), "net_inr")
        charges = exit_load + other_charges
        if kind == "REDEEM" and units * nav != gross:
            raise MutualFundImportError("redeemed MF units times assigned NAV do not equal gross value")
        if kind == "SUBSCRIBE" and units * nav != net:
            raise MutualFundImportError("allotted MF units times NAV do not equal invested value")
        expected_net = gross - charges if kind == "REDEEM" else gross - other_charges
        if net != expected_net or (kind == "SUBSCRIBE" and exit_load != 0):
            raise MutualFundImportError("MF gross value, charges and net value do not reconcile")

        event_date = _event_date(row.get("event_date"))
        recorded_at = _recorded_at(row.get("created_at"))
        effective_at = datetime.combine(event_date, time(23, 59, 59, 999999), tzinfo=_IST)
        instrument_id = mf_instrument_id(scheme, plan, option, isin)
        side = "BUY" if kind == "SUBSCRIBE" else "SELL"
        metadata: dict[str, object] = {
            "mf_event_id": event_id,
            "mf_event_sha256": digest,
            "mf_event_kind": kind,
            "scheme_code": scheme,
            "plan": plan,
            "option": option,
            "isin": isin,
            "event_date": event_date.isoformat(),
            "recorded_at": recorded_at.isoformat(),
            "folio_id": row.get("folio_id"),
            "evidence_scope": "LOCAL_MF_PAPER_BOOK_NOT_EXTERNAL_RECEIPT",
        }
        result.append(PortfolioEvent(
            account_id=account_id,
            sleeve_id=sleeve_id,
            source_system="MF_PAPER_BOOK",
            source_event_id=f"{event_id}:position",
            source_sha256=digest,
            event_kind=EventKind.FILL,
            occurred_at=effective_at.isoformat(),
            currency="INR",
            asset_class=AssetClass.MUTUAL_FUND,
            instrument_id=instrument_id,
            side=side,
            position_action=side,
            quantity=units,
            price=nav,
            metadata=metadata,
        ))
        if charges:
            result.append(PortfolioEvent(
                account_id=account_id,
                sleeve_id=sleeve_id,
                source_system="MF_PAPER_BOOK",
                source_event_id=f"{event_id}:charges",
                source_sha256=digest,
                event_kind=EventKind.FEE,
                occurred_at=effective_at.isoformat(),
                currency="INR",
                cash_delta=-charges,
                metadata={**metadata, "fee_type": "MF_TRANSACTION_CHARGES",
                          "exit_load_inr": str(exit_load),
                          "other_charges_inr": str(other_charges)},
            ))
        if kind == "REDEEM":
            # Confirmed NAV valuation closes units, but it does not make redemption
            # proceeds spendable. Reclassify them from cash to a receivable until an
            # explicit payout-available event is supplied.
            result.append(PortfolioEvent(
                account_id=account_id,
                sleeve_id=sleeve_id,
                source_system="MF_PAPER_BOOK",
                source_event_id=f"{event_id}:receivable",
                source_sha256=digest,
                event_kind=EventKind.CASH_RECEIVABLE,
                occurred_at=effective_at.isoformat(),
                currency="INR",
                cash_delta=-net,
                metadata={"action": "RECOGNIZE", "receivable_id": event_id,
                          "amount": str(net), "mf_event_sha256": digest,
                          "evidence_scope": "LOCAL_MF_PAPER_BOOK_NOT_EXTERNAL_PAYOUT"},
            ))
            matched_payout = payouts.get(event_id)
            if matched_payout is not None:
                del payouts[event_id]
                settlement_id = _require_text(matched_payout, "settlement_id")
                payout_hash = _require_text(matched_payout, "payout_source_sha256")
                if not _HASH.fullmatch(payout_hash):
                    raise MutualFundImportError("payout source hash must be a lowercase SHA-256 digest")
                payout_source = _require_text(matched_payout, "payout_source_id")
                payout_reference = _require_text(matched_payout, "payout_evidence_ref")
                available_at = _recorded_at(matched_payout.get("payout_available_at"))
                payout_amount = _amount(matched_payout.get("net_inr"), "payout net_inr", positive=True)
                if payout_amount != net or available_at <= effective_at:
                    raise MutualFundImportError("MF payout amount/time does not match its redemption claim")
                result.append(PortfolioEvent(
                    account_id=account_id,
                    sleeve_id=sleeve_id,
                    source_system="MF_PAPER_PAYOUT",
                    source_event_id=f"{settlement_id}:payout",
                    source_sha256=payout_hash,
                    event_kind=EventKind.CASH_RECEIVABLE,
                    occurred_at=available_at.isoformat(),
                    currency="INR",
                    cash_delta=payout_amount,
                    evidence_class="OBSERVED_PAYOUT_EVIDENCE",
                    metadata={"action": "SETTLE", "receivable_id": event_id,
                              "amount": str(payout_amount), "source_id": payout_source,
                              "source_reference": payout_reference,
                              "mf_settlement_id": settlement_id},
                ))
    if payouts:
        raise MutualFundImportError("payout evidence references a redemption absent from the MF event rows")
    return result


def import_mutual_fund_rows(
    rows: Sequence[Mapping[str, object]],
    canonical: CanonicalPortfolioLedger,
    *,
    account_id: str,
    sleeve_id: str = "MUTUAL_FUND",
    payout_rows: Sequence[Mapping[str, object]] = (),
) -> dict[str, int]:
    """Import an already-read set of MF paper rows; canonical journal handles replay."""
    events = events_from_mutual_fund_rows(
        rows, account_id=account_id, sleeve_id=sleeve_id, payout_rows=payout_rows,
    )
    return canonical.import_events(events)


__all__ = [
    "MutualFundImportError",
    "events_from_mutual_fund_rows",
    "import_mutual_fund_rows",
]
