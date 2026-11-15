"""Atomic point-in-time references to forward-event and PaperAccount journal heads."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

from financial_brain.forward_edge.durable import DurableJournal


class CheckpointError(ValueError):
    """A consistent immutable session checkpoint cannot be made or verified."""


def _validated_source(path: Path) -> tuple[bytes, list[dict]]:
    if not path.is_file():
        raise CheckpointError(f"checkpoint source journal is missing: {path.name}")
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise CheckpointError(f"checkpoint source journal has a torn tail: {path.name}")
    try:
        rows = [json.loads(line) for line in raw.splitlines()]
        head, sequence = "GENESIS", 0
        for row in rows:
            record = row["rec"]
            encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
            digest = hashlib.sha256((head + encoded).encode("utf-8")).hexdigest()
            if (row["seq"] != sequence + 1 or row["prev"] != head or row["hash"] != digest):
                raise ValueError("hash chain mismatch")
            head, sequence = digest, sequence + 1
        return raw, rows
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise CheckpointError(f"checkpoint source journal is corrupt: {path.name}") from exc
    raise AssertionError("unreachable")


def _prefix(path: Path, sequence: int) -> tuple[str, str]:
    raw, rows = _validated_source(path)
    if sequence < 0 or sequence > len(rows):
        raise CheckpointError("checkpoint sequence is outside the journal history")
    if sequence == 0:
        return "GENESIS", hashlib.sha256(b"").hexdigest()
    end = 0
    head = "GENESIS"
    for row, line in zip(rows, raw.splitlines(keepends=True)):
        head = row["hash"]
        end += len(line)
        if row["seq"] == sequence:
            return head, hashlib.sha256(raw[:end]).hexdigest()
    raise CheckpointError("checkpoint prefix sequence is not present in source journal")


def _stable_journal(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise CheckpointError(f"checkpoint source journal is missing: {path.name}")
    before = path.stat()
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise CheckpointError(f"checkpoint source journal has a torn tail: {path.name}")
    raw, rows = _validated_source(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(raw) != after.st_size:
        raise CheckpointError(f"checkpoint source changed while being captured: {path.name}")
    sequence = len(rows)
    journal_head = str(rows[-1]["hash"]) if rows else "GENESIS"
    head, prefix_sha256 = _prefix(path, sequence)
    if head != journal_head:
        raise CheckpointError(f"checkpoint journal head changed during capture: {path.name}")
    return {"sequence": sequence, "head_sha256": journal_head, "prefix_sha256": prefix_sha256,
            "byte_length": len(raw)}


def create_checkpoint(*, checkpoint_journal_path: Path, session_id: str, exchange_date: str,
                      phase: str, event_journal_path: Path, account_journal_path: Path,
                      captured_at_utc: datetime, writers_quiesced: bool) -> dict:
    """Fsync one record referencing both validated source journal prefixes.

    The caller must explicitly attest that both writers are quiesced while the
    two files are sampled. The implementation also checks each file is stable
    during its read; it cannot coordinate writers that ignore the caller's gate.
    """
    if writers_quiesced is not True:
        raise CheckpointError("both journal writers must be quiesced for a cross-journal checkpoint")
    if not session_id or phase not in {"START_OF_DAY", "END_OF_DAY"}:
        raise CheckpointError("session id and a supported checkpoint phase are required")
    try:
        day = date.fromisoformat(exchange_date).isoformat()
    except (TypeError, ValueError) as exc:
        raise CheckpointError("exchange_date must be canonical ISO date") from exc
    if captured_at_utc.tzinfo is None or captured_at_utc.utcoffset() is None:
        raise CheckpointError("checkpoint capture time must be timezone-aware")
    captured = captured_at_utc.astimezone(timezone.utc)
    checkpoint_path = Path(checkpoint_journal_path).resolve()
    event_path, account_path = Path(event_journal_path).resolve(), Path(account_journal_path).resolve()
    if event_path == account_path or checkpoint_path in {event_path, account_path}:
        raise CheckpointError("checkpoint, event, and account journals must use distinct paths")
    journal = DurableJournal(Path(checkpoint_journal_path))
    prior = next((r for r in journal.records() if r.get("t") == "SESSION_CHECKPOINT"
                  and r.get("session_id") == session_id and r.get("exchange_date") == day
                  and r.get("phase") == phase), None)
    if prior is not None:
        if not verify_checkpoint(prior, event_journal_path=event_path,
                                 account_journal_path=account_path):
            raise CheckpointError("prior checkpoint no longer verifies against its source journal prefixes")
        return prior
    events = _stable_journal(event_path)
    ledger = _stable_journal(account_path)
    identity = {"session_id": session_id, "exchange_date": day, "phase": phase,
                "captured_at_utc": captured.isoformat(), "event_journal": events,
                "account_journal": ledger}
    checkpoint_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"),
                                   allow_nan=False).encode()).hexdigest()
    record = {"t": "SESSION_CHECKPOINT", "checkpoint_id": checkpoint_id, **identity}
    journal.append(record)  # one durable hash-chain record contains both journal references
    return record


def verify_checkpoint(checkpoint: dict, *, event_journal_path: Path,
                      account_journal_path: Path) -> bool:
    """Verify that both referenced journal prefixes still exist byte-for-byte."""
    for key, path in (("event_journal", Path(event_journal_path)),
                      ("account_journal", Path(account_journal_path))):
        reference = checkpoint.get(key)
        if not isinstance(reference, dict):
            return False
        try:
            head, prefix_sha = _prefix(path, int(reference["sequence"]))
        except (CheckpointError, KeyError, TypeError, ValueError):
            return False
        if head != reference.get("head_sha256") or prefix_sha != reference.get("prefix_sha256"):
            return False
    return True


def verify_checkpoint_pair(start: dict, end: dict, *, event_journal_path: Path,
                           account_journal_path: Path) -> bool:
    """Verify start/end identity, time order, journal monotonicity, and both prefixes."""
    if (start.get("phase") != "START_OF_DAY" or end.get("phase") != "END_OF_DAY"
            or start.get("session_id") != end.get("session_id")
            or start.get("exchange_date") != end.get("exchange_date")):
        return False
    try:
        start_at = datetime.fromisoformat(str(start["captured_at_utc"]))
        end_at = datetime.fromisoformat(str(end["captured_at_utc"]))
        if (start_at.tzinfo is None or end_at.tzinfo is None or end_at <= start_at):
            return False
        for key in ("event_journal", "account_journal"):
            if int(end[key]["sequence"]) < int(start[key]["sequence"]):
                return False
    except (KeyError, TypeError, ValueError):
        return False
    return (verify_checkpoint(start, event_journal_path=event_journal_path,
                              account_journal_path=account_journal_path)
            and verify_checkpoint(end, event_journal_path=event_journal_path,
                                  account_journal_path=account_journal_path))
