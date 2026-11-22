"""Evidence-backed reconciliation of legacy MF lots without recorded folio identity."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from .mf_lot_book import MfPaperBookError


def _digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def reconcile_legacy_lot_folio(
    con,
    *,
    account_id: str,
    lot_id: str,
    folio_id: str,
    evidence_ref: str,
    evidence_sha256: str,
    reconciled_by: str,
    recorded_at: datetime,
) -> dict[str, Any]:
    """Assign an unowned legacy lot only with explicit, hash-pinned evidence.

    The function records the operator's evidence reference and digest; it does not
    independently verify a registrar statement or establish live-account ownership.
    """
    for label, value in (("account_id", account_id), ("lot_id", lot_id),
                         ("folio_id", folio_id), ("evidence_ref", evidence_ref),
                         ("reconciled_by", reconciled_by)):
        if not value.strip():
            raise MfPaperBookError(f"{label} is required")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", evidence_sha256):
        raise MfPaperBookError("evidence_sha256 must be a 64-character SHA-256 digest")
    if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
        raise MfPaperBookError("recorded_at must be timezone-aware")
    recorded = recorded_at.astimezone(timezone.utc)
    evidence_sha256 = evidence_sha256.lower()
    payload = {
        "account_id": account_id, "lot_id": lot_id, "folio_id": folio_id,
        "evidence_ref": evidence_ref, "evidence_sha256": evidence_sha256,
        "reconciled_by": reconciled_by, "recorded_at": recorded,
    }
    event_hash = _digest(payload)

    con.execute("BEGIN TRANSACTION")
    try:
        old = con.execute("""SELECT folio_id, evidence_ref, evidence_sha256,
            reconciled_by, recorded_at, event_sha256
            FROM mf_paper_folio_reconciliations WHERE account_id=? AND lot_id=?""",
            [account_id, lot_id]).fetchone()
        if old is not None:
            if tuple(old) != (folio_id, evidence_ref, evidence_sha256,
                              reconciled_by, recorded, event_hash):
                raise MfPaperBookError("legacy lot folio reconciliation is immutable")
            con.execute("COMMIT")
            return {**payload, "event_sha256": event_hash, "status": "already_reconciled"}

        current = con.execute("""SELECT folio_id FROM mf_paper_lots
            WHERE account_id=? AND lot_id=?""", [account_id, lot_id]).fetchone()
        if current is None:
            raise MfPaperBookError("legacy MF lot does not exist")
        if current[0] is not None:
            raise MfPaperBookError("lot already has a folio; reassignment is not supported")
        con.execute("""INSERT INTO mf_paper_folio_reconciliations (
            account_id, lot_id, folio_id, evidence_ref, evidence_sha256,
            reconciled_by, recorded_at, event_sha256) VALUES (?,?,?,?,?,?,?,?)""",
            [account_id, lot_id, folio_id, evidence_ref, evidence_sha256,
             reconciled_by, recorded, event_hash])
        con.execute("""UPDATE mf_paper_lots SET folio_id=?
            WHERE account_id=? AND lot_id=? AND folio_id IS NULL""",
            [folio_id, account_id, lot_id])
        if con.execute("""SELECT folio_id FROM mf_paper_lots
            WHERE account_id=? AND lot_id=?""", [account_id, lot_id]).fetchone() != (folio_id,):
            raise MfPaperBookError("legacy lot folio assignment did not persist")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {**payload, "event_sha256": event_hash, "status": "reconciled"}


__all__ = ["reconcile_legacy_lot_folio"]
