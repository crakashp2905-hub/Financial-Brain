"""Evidence-based status classification for legacy paper positions missing exits."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, cast

from .canonical_ledger import EventKind, _resolved_legacy_event_ids


class LegacyExitStatus(StrEnum):
    OPEN_UNKNOWN = "OPEN_UNKNOWN"
    LEDGER_INCOMPLETE = "LEDGER_INCOMPLETE"
    CONFIRMED_CLOSED = "CONFIRMED_CLOSED"


@dataclass(frozen=True, slots=True)
class LegacyExitCase:
    case_ref: str
    source_sha256: str
    classification: LegacyExitStatus
    reason_code: str
    matching_receipt_count: int
    exit_fill_inferred: bool = False


def classify_legacy_exits(events: Iterable[dict[str, object]]) -> tuple[LegacyExitCase, ...]:
    """Classify each unresolved event from the account's full canonical event set.

    A complete, source-bound receipt set is the only route to CONFIRMED_CLOSED. Related
    but incomplete receipts produce LEDGER_INCOMPLETE; absence of receipt evidence remains
    OPEN_UNKNOWN. Price paths and stale marks never count as fills.
    """
    rows = tuple(events)
    resolved_ids = _resolved_legacy_event_ids(list(rows))
    cases: list[LegacyExitCase] = []
    seen: set[tuple[str, str, str]] = set()
    for unresolved in rows:
        if unresolved.get("event_kind") != EventKind.UNRESOLVED_POSITION.value:
            continue
        event_id = unresolved.get("source_event_id")
        account_id = unresolved.get("account_id")
        source_system = unresolved.get("source_system")
        source_hash = unresolved.get("source_sha256")
        metadata = unresolved.get("metadata")
        if not all(isinstance(value, str) and value.strip()
                   for value in (event_id, account_id, source_system, source_hash)):
            raise ValueError("unresolved event identity and source hash are required")
        if not isinstance(metadata, dict):
            raise ValueError("unresolved event metadata must be an object")
        legacy_id = metadata.get("legacy_trade_id")
        if not isinstance(legacy_id, str) or not legacy_id.strip():
            raise ValueError("unresolved event must retain its legacy trade identity")
        event_id_text = cast(str, event_id)
        account_id_text = cast(str, account_id)
        source_system_text = cast(str, source_system)
        source_hash_text = cast(str, source_hash)
        key = (account_id_text, source_system_text, event_id_text)
        if key in seen:
            raise ValueError("duplicate unresolved event identity")
        seen.add(key)
        related: list[dict[str, object]] = []
        for candidate in rows:
            candidate_metadata = candidate.get("metadata")
            if (candidate.get("event_kind") == EventKind.FILL.value
                    and candidate.get("source_system") == "BROKER_EXECUTION_RECEIPT"
                    and isinstance(candidate_metadata, dict)
                    and (candidate_metadata.get("legacy_trade_id") == legacy_id
                         or candidate_metadata.get("resolves_unresolved_event_id") == event_id_text)):
                related.append(candidate)
        if event_id_text in resolved_ids:
            classification, reason = LegacyExitStatus.CONFIRMED_CLOSED, "EXACT_RECEIPT_SET_VERIFIED"
        elif related:
            classification, reason = LegacyExitStatus.LEDGER_INCOMPLETE, "RECEIPT_SET_INCOMPLETE_OR_CONFLICTING"
        else:
            classification, reason = LegacyExitStatus.OPEN_UNKNOWN, "NO_RECORDED_EXIT_OR_BROKER_RECEIPT"
        case_ref = hashlib.sha256(
            f"{account_id_text}\0{source_system_text}\0{event_id_text}".encode()).hexdigest()
        cases.append(LegacyExitCase(case_ref, source_hash_text, classification, reason, len(related)))
    return tuple(sorted(cases, key=lambda row: row.case_ref))


def report_body(*, events: Iterable[dict[str, object]], source_snapshot_sha256: str,
                snapshot_as_of_utc: str) -> dict[str, object]:
    """Build a trade-detail-free report pinned to the local ledger snapshot."""
    if len(source_snapshot_sha256) != 64 or any(c not in "0123456789abcdef" for c in source_snapshot_sha256):
        raise ValueError("source snapshot SHA-256 is invalid")
    rows = tuple(events)
    cases = classify_legacy_exits(rows)
    counts = {status.value: sum(case.classification is status for case in cases) for status in LegacyExitStatus}
    payload = {
        "schema": "fb.legacy-intraday-exit-adjudication.v1",
        "snapshot_as_of_utc": snapshot_as_of_utc,
        "source_snapshot_sha256": source_snapshot_sha256,
        "account_id": "intraday:live-v1:live",
        "scope_limitations": [
            "The report adjudicates the pinned canonical SQLite migration snapshot only; it does not re-read the original DuckDB source.",
            "No broker receipt artifact is authenticated or created by this report; absent exact stored receipt sets remain OPEN_UNKNOWN.",
        ],
        "case_count": len(cases),
        "classification_counts": counts,
        "cases": [{"case_ref": case.case_ref, "source_sha256": case.source_sha256,
                   "classification": case.classification.value, "reason_code": case.reason_code,
                   "matching_receipt_count": case.matching_receipt_count,
                   "exit_fill_inferred": case.exit_fill_inferred} for case in cases],
        "policy": "Only exact source-bound broker execution receipts confirm a close; marks and reconstructed bars never create fills.",
        "pnl_release_authorized": counts[LegacyExitStatus.OPEN_UNKNOWN.value] == 0
        and counts[LegacyExitStatus.LEDGER_INCOMPLETE.value] == 0,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    payload["report_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


__all__ = ["LegacyExitCase", "LegacyExitStatus", "classify_legacy_exits", "report_body"]
