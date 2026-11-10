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
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from financial_brain.forward_edge.market_data import Calendar

IST = timezone(timedelta(hours=5, minutes=30))


class SessionError(ValueError):
    pass


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
    """Unique packet ids, tolerant of reconnect replays and out-of-order delivery."""

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.buffer: list[tuple[int, str, dict]] = []
        self.duplicates = 0
        self.reconnects = 0

    def reconnect(self) -> None:
        self.reconnects += 1

    def offer(self, packet_id: str, seq: int, payload: dict) -> bool:
        if packet_id in self.seen:
            self.duplicates += 1
            return False
        self.seen.add(packet_id)
        self.buffer.append((seq, packet_id, payload))
        return True

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
    dest.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for f in files:
        shutil.copyfile(f, dest / f.name)
        manifest[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    (dest / "BACKUP_MANIFEST.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return manifest


def verify_restore(restored_dir: Path) -> list[str]:
    manifest = json.loads((restored_dir / "BACKUP_MANIFEST.json").read_text(encoding="utf-8"))
    problems = []
    for name, digest in sorted(manifest.items()):
        p = restored_dir / name
        if not p.exists():
            problems.append(f"missing {name}")
        elif hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            problems.append(f"mismatch {name}")
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
