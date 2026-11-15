"""Durable session-level EOD reconciliation gate for paper account entry control.

The gate persists the decision made from an independently supplied OMS/venue
snapshot. It does not fetch or attest that snapshot itself. A mismatch remains
an active entry halt across process restarts until a clean comparison and a
human signoff bound to the active incident are both recorded.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.kernel.paper_account_audit import (
    PaperAuditError,
    reconcile_eod_snapshot,
)


class EodGateError(ValueError):
    """The durable EOD gate cannot safely accept or replay the evidence."""


class PaperEodReconciliationGate:
    """Persist EOD reconciliation outcomes and an incident-bound entry halt."""

    def __init__(self, path: Path) -> None:
        self.journal = DurableJournal(Path(path))

    def _records(self) -> list[dict[str, Any]]:
        try:
            records = self.journal.records()
        except Exception as exc:
            raise EodGateError("EOD gate journal is corrupt or unavailable") from exc
        for record in records:
            if record.get("t") != "EOD_RECONCILIATION":
                raise EodGateError("unknown event in EOD gate journal")
        return records

    @staticmethod
    def _evidence_hash(payload: dict[str, Any]) -> str:
        try:
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                   allow_nan=False, default=str)
        except (TypeError, ValueError) as exc:
            raise EodGateError("EOD evidence is not canonical JSON") from exc
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def active_incident(self) -> str | None:
        """Return the persisted open mismatch incident, if any."""
        active: str | None = None
        for record in self._records():
            if record.get("incident_open") is True:
                active = record.get("incident_id")
            elif record.get("incident_open") is False and record.get("incident_id") == active:
                active = None
        return active

    def entries_allowed(self) -> bool:
        """Fail closed unless the latest durable reconciliation explicitly allows entries."""
        records = self._records()
        if not records:
            return False
        return records[-1].get("entries_allowed") is True and self.active_incident() is None

    def reconcile(
        self,
        *,
        session_date: date,
        journal_state: dict[str, Any],
        reference_positions: dict[str, int],
        reference_fill_ids: list[str],
        reference_marks: dict[str, dict[str, Any]],
        as_of: datetime,
        mark_tolerance: Any = "0.01",
        repair_signoff: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compare one frozen EOD snapshot and persist its result idempotently.

        A session can be replayed only with byte-equivalent canonical evidence.
        Reusing a session date with changed evidence raises rather than rewriting
        history. A prior incident clears only after a clean comparison and a
        signoff naming that exact incident and carrying a valid evidence hash.
        """
        if not isinstance(session_date, date) or isinstance(session_date, datetime):
            raise EodGateError("session_date must be an exchange date")
        evidence = {
            "session_date": session_date.isoformat(),
            "journal_state": journal_state,
            "reference_positions": reference_positions,
            "reference_fill_ids": reference_fill_ids,
            "reference_marks": reference_marks,
            "as_of": as_of.isoformat(),
            "mark_tolerance": str(mark_tolerance),
            "repair_signoff": repair_signoff,
        }
        evidence_sha256 = self._evidence_hash(evidence)
        records = self._records()
        prior_session = next((r for r in records if r.get("session_date") == session_date.isoformat()), None)
        if prior_session is not None:
            if prior_session.get("evidence_sha256") != evidence_sha256:
                raise EodGateError("EOD session is immutable; evidence changed on replay")
            return {key: value for key, value in prior_session.items() if key != "t"}

        incident_id = self.active_incident()
        signoff = repair_signoff
        if incident_id is not None:
            if signoff is not None and signoff.get("incident_id") != incident_id:
                raise EodGateError("repair signoff must name the active EOD incident")
        elif signoff is not None:
            raise EodGateError("repair signoff supplied without an active EOD incident")

        try:
            result = reconcile_eod_snapshot(
                journal_state=journal_state,
                reference_positions=reference_positions,
                reference_fill_ids=reference_fill_ids,
                reference_marks=reference_marks,
                as_of=as_of,
                mark_tolerance=mark_tolerance,
                prior_incident_open=incident_id is not None,
                repair_signoff=signoff,
            )
        except PaperAuditError as exc:
            raise EodGateError(str(exc)) from exc

        incident_open = incident_id is not None
        if result["status"] == "HALT_MISMATCH":
            if incident_id is None:
                incident_id = "eod-" + self._evidence_hash({"date": session_date.isoformat(),
                                                              "evidence": evidence_sha256})[:24]
            incident_open = True
        elif result["status"] == "RECONCILED_SIGNED_OFF":
            incident_open = False
        record = {
            "t": "EOD_RECONCILIATION",
            "session_date": session_date.isoformat(),
            "as_of": as_of.isoformat(),
            "evidence_sha256": evidence_sha256,
            "status": result["status"],
            "entries_allowed": result["entries_allowed"] and not incident_open,
            "mismatches": result["mismatches"],
            "incident_id": incident_id,
            "incident_open": incident_open,
        }
        self.journal.append(record)
        return {key: value for key, value in record.items() if key != "t"}
