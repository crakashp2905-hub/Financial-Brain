"""Session automation and reliability (FE-0069 .. FE-0078). Offline: injected clock, no feed, no broker.

Preflight blocks all intents when any check fails; the supervisor derives session state from an injected clock and a
venue calendar; opening ranges freeze only from complete windows; the decision journal is append-only and hash-chained;
packets are deduplicated and ordered; alerts fire once per root cause and halt entries; end-of-session checks find
forgotten inventory; completeness reports downgrade evidence when a segment is missing; backups restore to identical
hashes; runbooks are generated text.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.market_data import Calendar, DataError, FeedHealth, _aware

IST = timezone(timedelta(hours=5, minutes=30))


class SessionError(ValueError):
    pass


class SessionDateLedger:
    """Append-only outcome ledger so planned exchange dates cannot vanish silently."""

    def __init__(self, path: Path) -> None:
        self.journal = DurableJournal(Path(path))

    def _records(self) -> list[dict]:
        return self.journal.records()

    def records(self) -> list[dict]:
        """Return the append-only planned-session and terminal-outcome evidence."""
        return self._records()

    def is_planned(self, session_id: str) -> bool:
        return any(row.get("t") == "SESSION_DATE_PLAN" and row.get("session_id") == session_id
                   for row in self._records())

    def outcome(self, session_id: str) -> dict | None:
        return next((row for row in reversed(self._records())
                     if row.get("t") == "SESSION_DATE_RESULT" and row.get("session_id") == session_id), None)

    def plan_date(self, session_id: str, exchange_date: str, calendar: Calendar,
                  settlement_deadline_utc: datetime) -> dict:
        if not session_id:
            raise SessionError("session id is required")
        try:
            if date.fromisoformat(exchange_date).isoformat() != exchange_date:
                raise ValueError
        except ValueError as exc:
            raise SessionError("exchange date must be canonical ISO date") from exc
        if settlement_deadline_utc.tzinfo is None or settlement_deadline_utc.utcoffset() is None:
            raise SessionError("settlement deadline must be timezone-aware")
        deadline = settlement_deadline_utc.astimezone(timezone.utc)
        plan = {"t": "SESSION_DATE_PLAN", "session_id": session_id,
                "exchange_date": exchange_date, "calendar_version": calendar.version,
                "tradable": calendar.is_tradable(date.fromisoformat(exchange_date)),
                "settlement_deadline_utc": deadline.isoformat()}
        records = self._records()
        existing = next((row for row in records if row.get("t") == "SESSION_DATE_PLAN"
                         and row.get("session_id") == session_id), None)
        if existing is not None:
            if existing != plan:
                raise SessionError("planned exchange date is immutable")
        else:
            self.journal.append(plan)
        outcome = self.outcome(session_id)
        if outcome is None and not plan["tradable"]:
            return self.record_result(session_id, "NO_TRADE", "NON_TRADING_DATE")
        return outcome or {"session_id": session_id, "status": "PLANNED"}

    def record_result(self, session_id: str, status: str, reason: str) -> dict:
        if (status not in {"CLOSED", "NO_TRADE", "INCOMPLETE"}
                or not isinstance(reason, str) or not reason.strip()):
            raise SessionError("session date result needs CLOSED, NO_TRADE or INCOMPLETE and a reason")
        plan = next((row for row in self._records() if row.get("t") == "SESSION_DATE_PLAN"
                     and row.get("session_id") == session_id), None)
        if plan is None:
            raise SessionError("session date must be planned before it can be finalized")
        if not plan["tradable"] and (status != "NO_TRADE" or reason != "NON_TRADING_DATE"):
            raise SessionError("a non-trading date is immutable NO_TRADE")
        result = {"t": "SESSION_DATE_RESULT", "session_id": session_id,
                  "exchange_date": plan["exchange_date"], "status": status,
                  "reason": reason.strip()}
        prior = self.outcome(session_id)
        if prior is not None:
            if prior != result:
                raise SessionError("session date result is immutable")
            return prior
        self.journal.append(result)
        return result

    def expire_missing(self, now_utc: datetime) -> list[dict]:
        if now_utc.tzinfo is None or now_utc.utcoffset() is None:
            raise SessionError("sweep clock must be timezone-aware")
        now = now_utc.astimezone(timezone.utc)
        records = self._records()
        plans = [row for row in records if row.get("t") == "SESSION_DATE_PLAN" and row.get("tradable")]
        expired = []
        for plan in plans:
            if self.outcome(plan["session_id"]) is not None:
                continue
            deadline = datetime.fromisoformat(plan["settlement_deadline_utc"])
            if deadline <= now:
                expired.append(self.record_result(plan["session_id"], "INCOMPLETE", "SESSION_RESULT_MISSING"))
        return expired


# ---- FE-0069 preflight ----

@dataclass(frozen=True)
class PreflightReport:
    ok: bool
    failures: tuple[str, ...]
    checked_at_utc: datetime


REQUIRED_PREFLIGHT = ("calendar", "credentials", "disk", "feeds", "data_contracts", "risk", "system_health",
                      "broker_disabled")


def run_preflight(results: dict[str, bool], now: datetime) -> PreflightReport:
    failures = [name for name in REQUIRED_PREFLIGHT if results.get(name) is not True]
    return PreflightReport(not failures, tuple(failures), now.astimezone(timezone.utc))


def allowed_intents(report: PreflightReport, intents: list[dict]) -> list[dict]:
    """A blocked preflight leaves no paper intents; the report itself is still the artifact."""
    return list(intents) if report.ok else []


# ---- FE-0070 supervisor ----

@dataclass(frozen=True)
class SessionWindow:
    open_at: time = time(9, 15)
    close_at: time = time(15, 30)
    late_start_after: time = time(9, 20)


class SessionSupervisor:
    def __init__(self, calendar: Calendar, window: SessionWindow = SessionWindow()) -> None:
        self.calendar = calendar
        self.window = window

    def state(self, now: datetime) -> str:
        local = now.astimezone(IST)
        if not self.calendar.is_tradable(local.date()):
            return "HOLIDAY"
        t = local.time()
        if t < self.window.open_at:
            return "PREOPEN"
        if t >= self.window.close_at:
            return "CLOSED"
        return "LATE_START" if t > self.window.late_start_after else "OPEN"


# ---- FE-0071 opening range freeze ----

def freeze_opening_range(bars: list[tuple[datetime, float, float]], window_start: datetime, window_end: datetime,
                         as_of: datetime, *, minutes: int) -> dict:
    """``bars`` are (minute_start, high, low). Freeze only when the whole window is complete at ``as_of``."""
    if as_of < window_end:
        return {"status": "INCOMPLETE", "reason": "WINDOW_NOT_OVER"}
    inside = [b for b in bars if window_start <= b[0] < window_end]
    if len(inside) != minutes:
        return {"status": "INCOMPLETE", "reason": "MISSING_MINUTES"}
    return {"status": "FROZEN", "high": max(b[1] for b in inside), "low": min(b[2] for b in inside),
            "frozen_for_decisions_after": window_end.isoformat()}


def usable_after_restart(frozen_at: datetime | None, window_end: datetime, restarted_at: datetime) -> bool:
    """A range rebuilt after a restart from data that is now historical is not knowledge the strategy had live."""
    return frozen_at is not None and frozen_at <= restarted_at and frozen_at >= window_end


# ---- FE-0072 decision journal ----

class DecisionJournal:
    def __init__(self) -> None:
        self.records: list[dict] = []
        self.head = "GENESIS"

    def append(self, record: dict) -> str:
        body = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self.head = hashlib.sha256((self.head + body).encode("utf-8")).hexdigest()
        self.records.append(record)
        return self.head


def replay_hash(records: list[dict]) -> str:
    j = DecisionJournal()
    for r in records:
        j.append(r)
    return j.head


# ---- FE-0073 packet dedupe ----

class PacketDeduper:
    """Unique packet IDs, optionally recovering dedupe and health state from a durable journal.

    The journal stores only packet-ID hashes and sequencing metadata, never quote payloads.
    """

    def __init__(self, journal_path: Path | None = None) -> None:
        self.journal = DurableJournal(Path(journal_path)) if journal_path is not None else None
        self.seen: set[str] = set()
        self.buffer: list[tuple[int, str, dict]] = []
        self.duplicates = 0
        self.reconnects = 0
        self.disconnects = 0
        self.dropped_updates = 0
        self.last_heartbeat_utc: datetime | None = None
        self.last_available_utc: datetime | None = None
        self._last_sequence: int | None = None
        self.gaps = 0
        if self.journal is not None:
            self._restore(self.journal.records())

    @staticmethod
    def _packet_digest(packet_id: str) -> str:
        return hashlib.sha256(packet_id.encode("utf-8")).hexdigest()

    def _restore(self, records: list[dict]) -> None:
        for row in records:
            kind = row.get("t")
            if kind == "PACKET_OFFER":
                self._apply_offer(row["packet_id_sha256"], row["seq"], {},
                                  datetime.fromisoformat(row["available_utc"])
                                  if row.get("available_utc") else None)
            elif kind == "PACKET_DUPLICATE":
                self.duplicates += 1
            elif kind == "HEARTBEAT":
                self._apply_heartbeat(datetime.fromisoformat(row["at_utc"]))
            elif kind == "DISCONNECT":
                self.disconnects += 1
            elif kind == "RECONNECT":
                self.reconnects += 1
                self._last_sequence = None
            elif kind == "DROP":
                self.dropped_updates += row["count"]

    def _append(self, row: dict) -> None:
        if self.journal is not None:
            self.journal.append(row)

    def _apply_offer(self, packet_digest: str, seq: int, payload: dict,
                     available_utc: datetime | None) -> bool:
        if packet_digest in self.seen:
            self.duplicates += 1
            return False
        if self._last_sequence is not None and seq > self._last_sequence + 1:
            self.gaps += seq - self._last_sequence - 1
        if self._last_sequence is None or seq > self._last_sequence:
            self._last_sequence = seq
        if available_utc is not None:
            stamp = _aware(available_utc, "packet availability")
            if self.last_available_utc is None or stamp > self.last_available_utc:
                self.last_available_utc = stamp
        self.seen.add(packet_digest)
        self.buffer.append((seq, packet_digest, payload))
        return True

    def reconnect(self) -> None:
        self._append({"t": "RECONNECT"})
        self.reconnects += 1
        self._last_sequence = None

    def disconnect(self) -> None:
        self._append({"t": "DISCONNECT"})
        self.disconnects += 1

    def heartbeat(self, at: datetime) -> None:
        stamp = _aware(at, "heartbeat")
        if self.last_heartbeat_utc is not None and stamp < self.last_heartbeat_utc:
            raise DataError("heartbeat timestamps must be monotonic")
        self._append({"t": "HEARTBEAT", "at_utc": stamp.isoformat()})
        self._apply_heartbeat(stamp)

    def _apply_heartbeat(self, stamp: datetime) -> None:
        stamp = _aware(stamp, "heartbeat")
        if self.last_heartbeat_utc is not None and stamp < self.last_heartbeat_utc:
            raise DataError("heartbeat timestamps must be monotonic")
        self.last_heartbeat_utc = stamp

    def record_drop(self, count: int = 1) -> None:
        if type(count) is not int or count <= 0:
            raise DataError("drop count must be a positive integer")
        self._append({"t": "DROP", "count": count})
        self.dropped_updates += count

    def offer(self, packet_id: str, seq: int, payload: dict,
              available_utc: datetime | None = None) -> bool:
        if not packet_id or type(seq) is not int or seq < 0:
            raise DataError("packet id and non-negative integer sequence are required")
        digest = self._packet_digest(packet_id)
        available = _aware(available_utc, "packet availability") if available_utc is not None else None
        if digest in self.seen:
            self._append({"t": "PACKET_DUPLICATE", "packet_id_sha256": digest})
            self.duplicates += 1
            return False
        self._append({"t": "PACKET_OFFER", "packet_id_sha256": digest, "seq": seq,
                      "available_utc": available.isoformat() if available is not None else None})
        return self._apply_offer(digest, seq, payload, available)

    def feed_health(self) -> FeedHealth:
        return FeedHealth(self.last_available_utc, self.gaps, self.disconnects, self.dropped_updates,
                          self.duplicates, self.reconnects, self.last_heartbeat_utc)

    def ordered(self) -> list[dict]:
        return [p for _, _, p in sorted(self.buffer, key=lambda x: (x[0], x[1]))]


# ---- FE-0074 alerts ----

class AlertManager:
    def __init__(self) -> None:
        self.alerts: list[str] = []
        self._causes: set[str] = set()
        self.entries_halted = False

    def raise_alert(self, root_cause: str, *, halts_entries: bool = True) -> bool:
        if halts_entries:
            self.entries_halted = True
        if root_cause in self._causes:
            return False
        self._causes.add(root_cause)
        self.alerts.append(root_cause)
        return True


# ---- FE-0075 end-of-session ----

def end_of_session_check(positions: dict[str, int], *, intraday_only: bool, carry_allowed: set[str]) -> list[str]:
    problems = []
    for sym, qty in sorted(positions.items()):
        if qty == 0:
            continue
        if intraday_only:
            problems.append(f"FORGOTTEN_INVENTORY:{sym}:{qty}")
        elif sym not in carry_allowed:
            problems.append(f"UNAPPROVED_OVERNIGHT:{sym}:{qty}")
    return problems


# ---- FE-0076 completeness ----

SEGMENTS = ("preflight", "quotes", "decisions", "orders", "fills", "ledger", "close", "reconciliation")


def completeness_report(session_id: str, present: set[str], incidents: list[str]) -> dict:
    missing = [s for s in SEGMENTS if s not in present]
    eligible = not missing and not incidents
    return {"session_id": session_id, "missing_segments": missing, "incidents": sorted(incidents),
            "eligibility": "ELIGIBLE" if eligible else "NOT_ELIGIBLE_DOWNGRADED"}


# ---- FE-0077 backup and restore ----

def backup(files: list[Path], dest: Path) -> dict[str, str]:
    """Create a verified dependency-set bundle while preserving each relative path.

    All source files must share one base directory. Existing bundles are immutable; callers
    must choose a new destination instead of silently replacing previous evidence.
    """
    source_paths = [Path(f).resolve() for f in files]
    if not source_paths or len(set(source_paths)) != len(source_paths):
        raise SessionError("backup requires a non-empty set of unique source files")
    if any(not path.is_file() for path in source_paths):
        raise SessionError("backup source set contains a missing or non-file dependency")
    common = Path(os.path.commonpath([str(path.parent) for path in source_paths]))
    entries: list[dict[str, object]] = []
    for path in sorted(source_paths):
        try:
            relative = path.relative_to(common).as_posix()
        except ValueError as exc:
            raise SessionError("backup dependencies must share a common base directory") from exc
        if relative.startswith("../") or relative in {".", "BACKUP_MANIFEST.json"}:
            raise SessionError("backup dependency path is not safe")
        payload = path.read_bytes()
        entries.append({"path": relative, "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)})
    manifest = {"schema_version": "fb.offline-backup.v1", "files": entries}
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    destination = Path(dest)
    if destination.exists():
        raise SessionError("backup destination already exists; bundles are immutable")
    destination.mkdir(parents=True)
    try:
        for path, entry in zip(sorted(source_paths), entries):
            relative = str(entry["path"])
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        (destination / "BACKUP_MANIFEST.json").write_bytes(manifest_bytes)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return {str(entry["path"]): str(entry["sha256"]) for entry in entries}


def verify_restore(restored_dir: Path) -> list[str]:
    root = Path(restored_dir).resolve()
    try:
        manifest = json.loads((root / "BACKUP_MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["missing or invalid BACKUP_MANIFEST.json"]
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "fb.offline-backup.v1" or not isinstance(manifest.get("files"), list):
        return ["invalid backup manifest schema"]
    problems = []
    seen = set()
    for entry in manifest["files"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not isinstance(entry.get("sha256"), str):
            problems.append("invalid manifest file entry")
            continue
        name, digest = entry["path"], entry["sha256"]
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or name in seen or name == "BACKUP_MANIFEST.json":
            problems.append(f"unsafe or duplicate path {name}")
            continue
        seen.add(name)
        p = (root / relative).resolve()
        if root not in p.parents:
            problems.append(f"unsafe path {name}")
            continue
        if not p.exists():
            problems.append(f"missing {name}")
        elif hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            problems.append(f"mismatch {name}")
    if not seen:
        problems.append("backup manifest has no file entries")
    return problems


# ---- FE-0078 runbooks ----

RUNBOOKS = {
    "morning": ["Run preflight; confirm every check passed and broker is disabled.", "Confirm the venue calendar says today is a session.",
                "Confirm the charter and cohort schedule hashes match their pins.", "Confirm risk latches are clear."],
    "during": ["Watch feed health; an alert halts new entries automatically.", "Never reset a halted latch: restoring needs a human actor.",
               "Do not edit strategy parameters; a change is a new version and a new clock."],
    "evening": ["Run the end-of-session inventory check.", "Reconcile fills, cash and positions.",
                "Write the completeness report; any missing segment downgrades the session.", "Back up and verify the restore hashes."],
}


def render_runbook(phase: str) -> str:
    if phase not in RUNBOOKS:
        raise SessionError("unknown runbook phase")
    return f"# {phase.title()} runbook\n\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(RUNBOOKS[phase])) + "\n"
