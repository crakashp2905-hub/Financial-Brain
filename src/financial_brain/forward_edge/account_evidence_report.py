"""Tamper-evident account activity report with evidence classes kept separate."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from collections.abc import Mapping

from .custody import CustodyStatus, Receipt, TrustRoot, classify_custody


class AccountEvidenceError(ValueError):
    """Invalid activity evidence or broken account-report chain."""


class ActivityClass(StrEnum):
    RESEARCH = "RESEARCH"
    PAPER_FILL = "PAPER_FILL"
    HYPOTHETICAL = "HYPOTHETICAL"
    BROKER_ACTIVITY = "BROKER_ACTIVITY"


_SHA = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class AccountActivity:
    activity_id: str
    evidence_class: ActivityClass
    net_pnl_inr: float
    source_sha256: str
    manifest_sha256: str
    decision_at_utc: datetime
    earliest_entry_at_utc: datetime
    receipt: Receipt | None = None
    broker_activity_reference: str | None = None


def build_account_evidence_report(*, activities: tuple[AccountActivity, ...],
                                  trust_roots: Mapping[tuple[str, str], TrustRoot]) -> dict[str, object]:
    """Classify, verify and hash-chain account evidence without merging simulations.

    Only PAPER_FILL and cryptographically custody-verified BROKER_ACTIVITY count as
    account activity. Research and hypothetical outcomes are always excluded.
    """
    if len({row.activity_id for row in activities}) != len(activities):
        raise AccountEvidenceError("activity IDs must be unique")
    previous = "0" * 64
    chain = []
    totals = {key.value: {"count": 0, "net_pnl_inr": 0.0} for key in ActivityClass}
    account_pnl = 0.0
    for sequence, row in enumerate(activities, start=1):
        if not row.activity_id.strip() or not _SHA.fullmatch(row.source_sha256) \
                or not _SHA.fullmatch(row.manifest_sha256):
            raise AccountEvidenceError("activity identity and source/manifest SHA-256 are required")
        if not isinstance(row.evidence_class, ActivityClass):
            raise AccountEvidenceError("activity evidence class must be explicit")
        if not isinstance(row.net_pnl_inr, (int, float)) or not math.isfinite(row.net_pnl_inr):
            raise AccountEvidenceError("activity net P&L must be finite")
        disposition = "EXCLUDED_NON_ACCOUNT_EVIDENCE"
        custody_status = None
        if row.evidence_class is ActivityClass.PAPER_FILL:
            disposition = "PAPER_ACCOUNT_ACTIVITY"
            account_pnl += row.net_pnl_inr
        elif row.evidence_class is ActivityClass.BROKER_ACTIVITY:
            if row.source_sha256 != row.manifest_sha256:
                custody_status = CustodyStatus.CONFLICTED.value
            else:
                custody_status = classify_custody(
                    manifest_sha256=row.manifest_sha256,
                    decision_at_utc=row.decision_at_utc,
                    entry_earliest_at_utc=row.earliest_entry_at_utc,
                    receipt=row.receipt,
                    trust_roots=trust_roots,
                ).value
            if custody_status == CustodyStatus.VERIFIED.value and row.broker_activity_reference:
                disposition = "BROKER_VERIFIED_ACCOUNT_ACTIVITY"
                account_pnl += row.net_pnl_inr
            else:
                disposition = "BROKER_ACTIVITY_NOT_VERIFIED_EXCLUDED"
        totals[row.evidence_class.value]["count"] += 1
        totals[row.evidence_class.value]["net_pnl_inr"] += row.net_pnl_inr
        item = {
            "sequence": sequence,
            "activity_id": row.activity_id,
            "evidence_class": row.evidence_class.value,
            "disposition": disposition,
            "net_pnl_inr": row.net_pnl_inr,
            "source_sha256": row.source_sha256,
            "manifest_sha256": row.manifest_sha256,
            "custody_status": custody_status,
            "broker_activity_reference": row.broker_activity_reference,
            "previous_sha256": previous,
        }
        digest = hashlib.sha256(json.dumps(item, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode()).hexdigest()
        chain.append({**item, "record_sha256": digest})
        previous = digest
    body: dict[str, object] = {
        "schema": "fb.account_evidence_report.v1",
        "activity_count": len(activities),
        "class_totals": totals,
        "verified_account_activity_net_pnl_inr": account_pnl,
        "research_and_hypothetical_excluded_from_account_pnl": True,
        "broker_activity_counts_as_verified_only_with_pinned_ed25519_custody": True,
        "chain_head_sha256": previous,
        "records": chain,
    }
    body["report_sha256"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()
    return body


def verify_account_evidence_report(report: Mapping[str, object]) -> bool:
    """Verify each local record hash, predecessor link and report checksum."""
    records = report.get("records")
    if not isinstance(records, list):
        return False
    previous = "0" * 64
    for sequence, record in enumerate(records, start=1):
        if not isinstance(record, dict) or record.get("sequence") != sequence \
                or record.get("previous_sha256") != previous:
            return False
        core = {key: value for key, value in record.items() if key != "record_sha256"}
        digest = hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode()).hexdigest()
        if record.get("record_sha256") != digest:
            return False
        previous = digest
    if report.get("chain_head_sha256") != previous:
        return False
    body = {key: value for key, value in report.items() if key != "report_sha256"}
    return report.get("report_sha256") == hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()
