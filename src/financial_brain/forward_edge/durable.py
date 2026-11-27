"""Atomic append-only durability primitives shared by the forward-session runner."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


class DurableJournalError(ValueError):
    """Durable journal rejected conflicting or malformed evidence."""


class DurableSessionJournal:
    """Append lifecycle events and their audit records in the runner's SQLite database."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.execute("""CREATE TABLE IF NOT EXISTS runner_audit_log (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            record_id TEXT NOT NULL, kind TEXT NOT NULL, occurred_at TEXT NOT NULL,
            record_sha256 TEXT NOT NULL, record_json TEXT NOT NULL,
            previous_event_sha256 TEXT NOT NULL DEFAULT '', event_sha256 TEXT NOT NULL DEFAULT '',
            UNIQUE(session_id, record_id))""")
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(runner_audit_log)")}
        # Older journals remain readable, but backfill a deterministic chain before writes.
        if "previous_event_sha256" not in columns:
            self.connection.execute("ALTER TABLE runner_audit_log ADD COLUMN previous_event_sha256 TEXT NOT NULL DEFAULT ''")
        if "event_sha256" not in columns:
            self.connection.execute("ALTER TABLE runner_audit_log ADD COLUMN event_sha256 TEXT NOT NULL DEFAULT ''")
        # Existing databases may already have immutability triggers from the older schema.
        self.connection.executescript("DROP TRIGGER IF EXISTS runner_audit_no_update; DROP TRIGGER IF EXISTS runner_audit_no_delete;")
        try:
            self._backfill_hash_chains()
        finally:
            self.connection.executescript("""
            CREATE TRIGGER IF NOT EXISTS runner_audit_no_update
            BEFORE UPDATE ON runner_audit_log BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS runner_audit_no_delete
            BEFORE DELETE ON runner_audit_log BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
            """)

    @staticmethod
    def canonical(record: dict[str, Any]) -> tuple[str, str]:
        try:
            encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise DurableJournalError(f"record is not canonical JSON: {exc}") from exc
        return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _chain_hash(previous: str, sequence: int, session_id: str, record_id: str,
                    kind: str, occurred_at: str, record_sha256: str) -> str:
        body = json.dumps({"sequence": sequence, "session_id": session_id, "record_id": record_id,
                           "kind": kind, "occurred_at": occurred_at, "record_sha256": record_sha256,
                           "previous_event_sha256": previous}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def _backfill_hash_chains(self) -> None:
        sessions = self.connection.execute("SELECT DISTINCT session_id FROM runner_audit_log").fetchall()
        for (session_id,) in sessions:
            previous = "0" * 64
            rows = self.connection.execute(
                "SELECT sequence,record_id,kind,occurred_at,record_sha256,previous_event_sha256,event_sha256 "
                "FROM runner_audit_log WHERE session_id=? ORDER BY sequence", [session_id]).fetchall()
            for sequence, record_id, kind, occurred_at, record_sha, prior, event_hash in rows:
                expected = self._chain_hash(previous, sequence, session_id, record_id, kind, occurred_at, record_sha)
                if event_hash and (prior != previous or event_hash != expected):
                    raise DurableJournalError(f"audit hash chain is corrupt for session {session_id} sequence {sequence}")
                if not event_hash:
                    self.connection.execute(
                        "UPDATE runner_audit_log SET previous_event_sha256=?,event_sha256=? WHERE sequence=?",
                        [previous, expected, sequence])
                previous = expected

    def append_audit(self, *, session_id: str, kind: str, record_id: str,
                     occurred_at: datetime, record: dict[str, Any]) -> bool:
        if not session_id.strip() or not kind.strip() or not record_id.strip():
            raise DurableJournalError("audit session, kind and record identity are required")
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise DurableJournalError("audit timestamp must be timezone aware")
        canonical, digest = self.canonical(record)
        stable_id = f"{kind}:{record_id}"
        previous = self.connection.execute(
            "SELECT record_sha256 FROM runner_audit_log WHERE session_id=? AND record_id=?",
            [session_id, stable_id]).fetchone()
        if previous:
            if previous[0] != digest:
                raise DurableJournalError("append-only audit record conflicts with prior evidence")
            return False
        head_row = self.connection.execute(
            "SELECT event_sha256 FROM runner_audit_log WHERE session_id=? ORDER BY sequence DESC LIMIT 1",
            [session_id]).fetchone()
        chain_previous = head_row[0] if head_row else "0" * 64
        occurred = occurred_at.astimezone(timezone.utc).isoformat()
        sequence = int(self.connection.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM runner_audit_log").fetchone()[0])
        event_hash = self._chain_hash(chain_previous, sequence, session_id, stable_id, kind, occurred, digest)
        self.connection.execute(
            "INSERT INTO runner_audit_log "
            "(session_id, record_id, kind, occurred_at, record_sha256, record_json, previous_event_sha256, event_sha256) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [session_id, stable_id, kind, occurred, digest, canonical, chain_previous, event_hash])
        return True

    def append_lifecycle_record(self, *, session_id: str, kind: str, event_id: str,
                                occurred_at: datetime, record: dict[str, Any]) -> bool:
        """Atomically persist the materialized lifecycle event and its immutable audit entry."""
        if not session_id.strip() or not kind.strip() or not event_id.strip():
            raise DurableJournalError("lifecycle session, kind and event identity are required")
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise DurableJournalError("lifecycle timestamp must be timezone aware")
        canonical, digest = self.canonical(record)
        event_key = f"{session_id}:{kind}"
        event_time = occurred_at.astimezone(timezone.utc).isoformat()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            prior = self.connection.execute(
                "SELECT event_sha256 FROM runner_events WHERE session_id=? AND event_id=?",
                [session_id, event_key]).fetchone()
            if prior and prior[0] != digest:
                raise DurableJournalError("session lifecycle record conflicts with prior evidence")
            if not prior:
                self.connection.execute(
                    "INSERT INTO runner_events (session_id, event_id, kind, available_at, ingested_at, "
                    "ingest_clock_source, event_sha256, event_json) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)",
                    [session_id, event_key, kind, event_time, digest, canonical])
            self.append_audit(session_id=session_id, kind=kind, record_id=event_key,
                              occurred_at=occurred_at, record=record)
            self.connection.commit()
            return prior is None
        except Exception:
            self.connection.rollback()
            raise

    def audit_records(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT sequence, record_id, kind, occurred_at, record_sha256, record_json, "
            "previous_event_sha256,event_sha256 "
            "FROM runner_audit_log WHERE session_id=? ORDER BY sequence", [session_id]).fetchall()
        result = []
        previous = "0" * 64
        for row in rows:
            sequence, record_id, kind, occurred, record_sha, record_json, prior, event_hash = row
            try:
                payload = json.loads(record_json)
            except json.JSONDecodeError as exc:
                raise DurableJournalError("audit record JSON is corrupt") from exc
            _, actual_record_sha = self.canonical(payload)
            expected = self._chain_hash(previous, sequence, session_id, record_id, kind, occurred, record_sha)
            if actual_record_sha != record_sha or prior != previous or event_hash != expected:
                raise DurableJournalError(f"audit hash chain verification failed for sequence {sequence}")
            result.append({"kind": kind, "record_id": record_id, "occurred_at": occurred,
                           "record_sha256": record_sha, "previous_event_sha256": prior,
                           "event_sha256": event_hash, "record": payload})
            previous = event_hash
        return result


__all__ = ["DurableJournalError", "DurableSessionJournal"]


# ---- merged from main (2026-11-18): definitions main's modules depend on; the branch's own
# ---- definitions above are unchanged.
import os
from pathlib import Path


class JournalCorrupt(RuntimeError):
    pass


class Crash(RuntimeError):
    """Injected power loss."""


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _link(prev: str, rec: dict) -> str:
    return hashlib.sha256((prev + _canon(rec)).encode("utf-8")).hexdigest()


class DurableJournal:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.seq = 0
        self.head = "GENESIS"
        self.torn_tail_discarded = False
        self._recover()

    def _recover(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_bytes(b"")
            return
        raw = self.path.read_bytes()
        good_end = 0
        offset = 0
        while offset < len(raw):
            nl = raw.find(b"\n", offset)
            if nl == -1:                      # no terminator: torn final write
                self.torn_tail_discarded = True
                break
            try:
                row = json.loads(raw[offset:nl].decode("utf-8"))
                ok = (row["seq"] == self.seq + 1 and row["prev"] == self.head and row["hash"] == _link(self.head, row["rec"]))
            except (ValueError, KeyError, TypeError):
                ok = False
            if not ok:
                # A newline-terminated record that fails validation is corruption or tampering, never a torn write:
                # only an unterminated tail can be a power-loss artefact.
                raise JournalCorrupt(f"record {self.seq + 1} is corrupt or out of chain")
            self.seq, self.head = row["seq"], row["hash"]
            offset = good_end = nl + 1
        if good_end < len(raw):
            with self.path.open("r+b") as fh:
                fh.truncate(good_end)
                fh.flush()
                os.fsync(fh.fileno())

    def append(self, rec: dict) -> int:
        return self.append_many([rec])[0]

    def append_many(self, records: list[dict]) -> list[int]:
        """Append a batch with one fsync while preserving the same per-record hash chain."""
        if not records:
            return []
        seq, head = self.seq, self.head
        lines = []
        sequences = []
        for rec in records:
            seq += 1
            digest = _link(head, rec)
            lines.append(_canon({"seq": seq, "prev": head, "hash": digest, "rec": rec}) + "\n")
            sequences.append(seq)
            head = digest
        with self.path.open("ab") as fh:
            fh.write("".join(lines).encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        self.seq, self.head = seq, head
        return sequences

    def append_torn(self, rec: dict) -> None:
        """Test hook: simulate power loss in the middle of a write (half a line, no terminator)."""
        line = _canon({"seq": self.seq + 1, "prev": self.head, "hash": _link(self.head, rec), "rec": rec})
        with self.path.open("ab") as fh:
            fh.write(line[: len(line) // 2].encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())

    def records(self) -> list[dict]:
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line)["rec"])
        return out


def fold(journal: DurableJournal) -> dict:
    """Rebuild economic state from records alone. Fills are keyed by fill id, so a replayed record cannot double-count."""
    state: dict = {"receipt": False, "signal": None, "risk": None, "outbox": {}, "fills": {}, "ledger": False, "cash": "0"}
    snapshot_seen = False
    for r in journal.records():
        t = r["t"]
        if t == "SNAPSHOT":
            state = json.loads(json.dumps(r["state"]))
            snapshot_seen = True
        elif t == "RECEIPT":
            state["receipt"] = True
        elif t == "SIGNAL":
            state["signal"] = r["side"]
        elif t == "RISK":
            state["risk"] = r["decision"]
        elif t == "OUTBOX":
            state["outbox"][r["key"]] = r["state"]
        elif t == "FILL":
            state["fills"].setdefault(r["fill_id"], {"qty": r["qty"], "price": r["price"]})
        elif t == "LEDGER":
            state["ledger"] = True
            state["cash"] = r["cash"]
    state["_snapshot_seen"] = snapshot_seen
    return state


def state_hash(state: dict) -> str:
    clean = {k: v for k, v in state.items() if not k.startswith("_")}
    return hashlib.sha256(_canon(clean).encode("utf-8")).hexdigest()


class SimBroker:
    """In-memory venue stand-in. It remembers what it received, which is what reconciliation asks it."""

    def __init__(self) -> None:
        self.received: list[str] = []

    def receive(self, key: str) -> None:
        self.received.append(key)

    def has(self, key: str) -> bool:
        return key in self.received

    def fill_for(self, key: str) -> dict | None:
        return {"fill_id": f"{key}:f1", "qty": 10, "price": "100.00"} if key in self.received else None


STAGES = ("receipt", "signal", "risk", "outbox_pending", "send", "ack", "fill", "ledger")


MODES = ("before", "torn", "after")


def run_session(journal: DurableJournal, broker: SimBroker, key: str, *, crash_at: str | None = None,
                mode: str = "after") -> dict:
    """Run the staged flow once. ``crash_at``/``mode`` inject power loss at a stage. Raises ``Crash`` when injected."""
    st = fold(journal)

    def write(stage: str, rec: dict) -> None:
        if crash_at == stage and mode == "before":
            raise Crash(stage)
        if crash_at == stage and mode == "torn":
            journal.append_torn(rec)
            raise Crash(stage)
        journal.append(rec)
        if crash_at == stage and mode == "after":
            raise Crash(stage)

    if not st["receipt"]:
        write("receipt", {"t": "RECEIPT"})
    if st["signal"] is None:
        write("signal", {"t": "SIGNAL", "side": "BUY"})
    if st["risk"] is None:
        write("risk", {"t": "RISK", "decision": "ALLOW"})
    outbox = st["outbox"].get(key)
    if outbox is None:
        write("outbox_pending", {"t": "OUTBOX", "key": key, "state": "PENDING"})
        outbox = "PENDING"
    if outbox == "PENDING":
        if crash_at == "send" and mode == "before":
            raise Crash("send")
        broker.receive(key)                          # the external effect
        if crash_at == "send" and mode == "effect_only":
            raise Crash("send")
        write("send", {"t": "OUTBOX", "key": key, "state": "SENT"})
        outbox = "SENT"
    if outbox == "SENT":
        write("ack", {"t": "OUTBOX", "key": key, "state": "ACKED"})
    st = fold(journal)
    if st["outbox"].get(key) == "ACKED" and not st["fills"]:
        fill = broker.fill_for(key)
        if fill is not None:
            write("fill", {"t": "FILL", **fill})
    st = fold(journal)
    if st["fills"] and not st["ledger"]:
        cash = str(-sum(f["qty"] * float(f["price"]) for f in st["fills"].values()))
        write("ledger", {"t": "LEDGER", "cash": cash})
    return fold(journal)


def recover(journal: DurableJournal, broker: SimBroker, key: str) -> dict:
    """After a restart: reconcile any non-final outbox entry against the venue, then finish the flow. Never blind-resend."""
    st = fold(journal)
    if st["outbox"].get(key) in ("PENDING", "SENT"):
        journal.append({"t": "OUTBOX", "key": key, "state": "UNKNOWN"})
        if broker.has(key):
            journal.append({"t": "OUTBOX", "key": key, "state": "SENT"})      # venue confirms it took effect: do not resend
        else:
            journal.append({"t": "OUTBOX", "key": key, "state": "PENDING"})   # venue confirms it did not: sending is now safe
    elif st["outbox"].get(key) == "UNKNOWN":
        if broker.has(key):
            journal.append({"t": "OUTBOX", "key": key, "state": "SENT"})
        else:
            journal.append({"t": "OUTBOX", "key": key, "state": "PENDING"})
    return run_session(journal, broker, key)


def rollover(journal: DurableJournal, archive_path: Path) -> str:
    """Compact: archive the old segment, atomically start a new one whose first record is a state snapshot."""
    st = fold(journal)
    before = state_hash(st)
    clean = {k: v for k, v in st.items() if not k.startswith("_")}
    tmp = journal.path.with_suffix(".compact.tmp")
    snap = {"t": "SNAPSHOT", "state": clean}
    line = _canon({"seq": 1, "prev": "GENESIS", "hash": _link("GENESIS", snap), "rec": snap}) + "\n"
    with tmp.open("wb") as fh:
        fh.write(line.encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(journal.path, archive_path)
    os.replace(tmp, journal.path)
    journal.seq, journal.head = 1, _link("GENESIS", snap)
    after = state_hash(fold(journal))
    if after != before:
        raise JournalCorrupt("state hash changed across rollover")
    return after


def backup(journal: DurableJournal, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = journal.path.read_bytes()
    dest.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def restore(backup_path: Path, target: Path) -> DurableJournal:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(backup_path.read_bytes())
    return DurableJournal(target)

