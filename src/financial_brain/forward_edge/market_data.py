"""Market-session capture and data integrity (FE-0033 .. FE-0044). Offline: fixtures only, no live feed.

* Normalized events keep UTC event, received, available, and optional ingest clocks; decisions use availability time.
* Venue calendar from an explicit, versioned file; no weekday guessing (FE-0034). The real NSE holiday list is not
  bundled here: that is a named input, see tracker BLK-DATA / calendar note.
* Fixture feed replays normalized events; the live Kite adapter is not implemented (FE-0035, blocked).
* Append-only, hash-chained quote archive with capture-loss counters (FE-0036).
* Data age, gaps and disconnects trigger abstention; nothing is forward-filled (FE-0037).
* Decision snapshots hash every input they used (FE-0038). Instrument master gate (FE-0039).
* Option intents require two-sided executable quotes (FE-0040). Minute and close divergences are recorded, never
  silently corrected (FE-0041). Data gate reason codes are deterministic (FE-0042).
* Features use only data at or before their timestamp (FE-0043). Coverage is reported with every claim (FE-0044).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


class DataError(ValueError):
    pass


KINDS = frozenset({"TICK", "QUOTE", "DEPTH", "BAR", "HALT", "AUCTION", "CORP_ACTION",
                   "BROKER_UPDATE", "BOOK_POSITION"})


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DataError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class NormalizedEvent:
    kind: str
    instrument: str
    event_utc: datetime
    received_utc: datetime
    available_utc: datetime
    payload: tuple[tuple[str, str], ...] = ()
    ingested_utc: datetime | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise DataError(f"unknown kind {self.kind}")
        e = _aware(self.event_utc, "event")
        r = _aware(self.received_utc, "received")
        a = _aware(self.available_utc, "available")
        if not (e <= r <= a):
            raise DataError("clocks must satisfy event <= received <= available")
        if self.ingested_utc is not None and _aware(self.ingested_utc, "ingested") < a:
            raise DataError("ingest clock cannot precede source availability")

    def content_sha256(self) -> str:
        body_obj = {"kind": self.kind, "instrument": self.instrument,
                           "event": _aware(self.event_utc, "e").isoformat(),
                           "received": _aware(self.received_utc, "r").isoformat(),
                           "available": _aware(self.available_utc, "a").isoformat(),
                           "payload": [list(p) for p in self.payload]}
        if self.ingested_utc is not None:
            body_obj["ingested"] = _aware(self.ingested_utc, "ingested").isoformat()
        body = json.dumps(body_obj, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def decision_visible(event: NormalizedEvent, decision_utc: datetime) -> bool:
    """An event may inform a decision only if it was available then (not merely event-stamped then)."""
    return _aware(event.available_utc, "a") <= _aware(decision_utc, "decision")


def executable_quote(event: NormalizedEvent) -> tuple[bool, str]:
    """Return whether normalized quote evidence contains a valid two-sided displayed market."""
    payload = dict(event.payload)
    if event.kind != "QUOTE":
        return False, "NOT_A_QUOTE"
    try:
        bid, ask = float(payload["bid_price"]), float(payload["ask_price"])
        bid_qty, ask_qty = int(payload["bid_qty"]), int(payload["ask_qty"])
    except (KeyError, TypeError, ValueError):
        return False, "LTP_ONLY_OR_MISSING_DEPTH"
    if bid <= 0 or ask < bid or ask_qty <= 0 or bid_qty <= 0:
        return False, "INVALID_OR_EMPTY_BOOK"
    return True, "EXECUTABLE_TOP_OF_BOOK"


# ---- calendar (FE-0034) ----

@dataclass(frozen=True)
class Calendar:
    version: str
    holidays: frozenset[date]
    special_sessions: frozenset[date] = frozenset()

    def is_tradable(self, day: date) -> bool:
        if day in self.special_sessions:
            return True
        return day.weekday() < 5 and day not in self.holidays

    def next_tradable(self, after: date) -> date:
        day = after + timedelta(days=1)
        for _ in range(60):
            if self.is_tradable(day):
                return day
            day += timedelta(days=1)
        raise DataError("no tradable session within 60 days: calendar is wrong")


def load_calendar(path: Path) -> Calendar:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "version" not in data or "holidays" not in data:
        raise DataError("calendar needs version and holidays")
    return Calendar(version=str(data["version"]),
                    holidays=frozenset(date.fromisoformat(d) for d in data["holidays"]),
                    special_sessions=frozenset(date.fromisoformat(d) for d in data.get("special_sessions", [])))


# ---- fixture feed (FE-0035 offline half) ----

def fixture_feed(path: Path) -> list[NormalizedEvent]:
    """Replay normalized events from a JSON fixture, in (event, sequence) order."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    events = []
    for row in rows:
        events.append(NormalizedEvent(
            kind=row["kind"], instrument=row["instrument"],
            event_utc=datetime.fromisoformat(row["event_utc"]),
            received_utc=datetime.fromisoformat(row["received_utc"]),
            available_utc=datetime.fromisoformat(row["available_utc"]),
            payload=tuple(sorted((k, str(v)) for k, v in row.get("payload", {}).items())),
        ))
    return sorted(events, key=lambda e: (e.event_utc, e.instrument, e.kind))


# ---- append-only hash-chained archive (FE-0036) ----

class QuoteArchive:
    """Append-only JSONL. Each record names the previous record's hash; an edited or dropped record breaks the chain."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self.dropped_updates = 0

    def _last_hash(self) -> str:
        lines = [ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return json.loads(lines[-1])["hash"] if lines else "GENESIS"

    def append(self, event: NormalizedEvent) -> str:
        prev = self._last_hash()
        digest = hashlib.sha256((prev + event.content_sha256()).encode("utf-8")).hexdigest()
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"prev": prev, "hash": digest, "content": event.content_sha256()}, sort_keys=True) + "\n")
        return digest

    def record_drop(self) -> None:
        self.dropped_updates += 1

    def verify(self) -> bool:
        prev = "GENESIS"
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row["prev"] != prev:
                return False
            if hashlib.sha256((prev + row["content"]).encode("utf-8")).hexdigest() != row["hash"]:
                return False
            prev = row["hash"]
        return True


# ---- health gate: abstain, never forward-fill (FE-0037) ----

@dataclass(frozen=True)
class FeedHealth:
    last_available_utc: datetime | None
    gaps: int
    disconnects: int
    dropped_updates: int
    duplicates: int = 0
    reconnects: int = 0
    last_heartbeat_utc: datetime | None = None

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 0 for value in (
                self.gaps, self.disconnects, self.dropped_updates, self.duplicates, self.reconnects)):
            raise DataError("feed-health counters must be non-negative integers")
        for label, value in (("last available", self.last_available_utc),
                             ("last heartbeat", self.last_heartbeat_utc)):
            if value is not None:
                _aware(value, label)


def entry_allowed(health: FeedHealth, now: datetime, *, max_age: timedelta = timedelta(seconds=5)) -> tuple[bool, list[str]]:
    reasons = []
    if health.last_available_utc is None:
        reasons.append("NO_QUOTES")
    else:
        quote_age = _aware(now, "now") - _aware(health.last_available_utc, "last")
        if quote_age < timedelta(0):
            reasons.append("FUTURE_QUOTE")
        elif quote_age > max_age:
            reasons.append("STALE_QUOTE")
    if health.last_heartbeat_utc is not None:
        heartbeat_age = _aware(now, "now") - _aware(health.last_heartbeat_utc, "heartbeat")
        if heartbeat_age < timedelta(0):
            reasons.append("FUTURE_HEARTBEAT")
        elif heartbeat_age > max_age:
            reasons.append("STALE_HEARTBEAT")
    if health.gaps:
        reasons.append("QUOTE_GAP")
    if health.disconnects:
        reasons.append("DISCONNECTED")
    if health.dropped_updates:
        reasons.append("DROPPED_UPDATES")
    return (not reasons), reasons


# ---- decision snapshot (FE-0038) ----

@dataclass(frozen=True)
class DecisionSnapshot:
    decision_utc: datetime
    inputs: tuple[tuple[str, str], ...]  # (name, sha256) for every input the strategy read

    def sha256(self) -> str:
        body = json.dumps({"t": _aware(self.decision_utc, "d").isoformat(), "inputs": [list(i) for i in self.inputs]},
                          sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ---- instrument master (FE-0039) ----

@dataclass(frozen=True)
class Instrument:
    isin: str
    symbol: str
    lot: int
    tick: str
    status: str  # ACTIVE, SUSPENDED, DELISTED
    rights_ok: bool
    expiry: date | None = None


def instrument_gate(inst: Instrument, as_of: date) -> list[str]:
    reasons = []
    if inst.status != "ACTIVE":
        reasons.append("NOT_TRADABLE_STATUS")
    if inst.expiry is not None and inst.expiry <= as_of:
        reasons.append("EXPIRED")
    if inst.lot <= 0:
        reasons.append("BAD_LOT")
    if not inst.rights_ok:
        reasons.append("NO_DATA_RIGHTS")
    return reasons


# ---- options (FE-0040) ----

def option_quote_executable(bid: float | None, ask: float | None, bid_qty: int, ask_qty: int) -> tuple[bool, str]:
    if bid is None or ask is None:
        return False, "ONE_SIDED_OR_MID_ONLY"
    if bid <= 0 or ask <= 0 or ask < bid:
        return False, "CROSSED_OR_INVALID"
    if bid_qty <= 0 or ask_qty <= 0:
        return False, "NO_DEPTH"
    return True, "EXECUTABLE"


# ---- EOD reconciliation (FE-0041) ----

def reconcile_close(live_close: float, authoritative_close: float, *, tolerance: float = 0.0) -> dict:
    """Record divergence as data. Nothing is overwritten."""
    diff = authoritative_close - live_close
    status = "MATCH" if abs(diff) <= tolerance else "DIVERGED"
    return {"status": status, "live_close": live_close, "authoritative_close": authoritative_close,
            "difference": round(diff, 10), "version": "close-recon-v1"}


# ---- data gate reason codes (FE-0042) ----

def data_gate(*, age_s: float, spread_bps: float | None, volume: int, circuit_hit: bool, frozen: bool,
              auction_seen: bool, max_age_s: float = 5.0, max_spread_bps: float = 50.0) -> list[str]:
    codes = []
    if age_s > max_age_s:
        codes.append("STALE")
    if spread_bps is None or spread_bps > max_spread_bps:
        codes.append("WIDE_SPREAD")
    if volume <= 0:
        codes.append("ZERO_VOLUME")
    if circuit_hit:
        codes.append("CIRCUIT")
    if frozen:
        codes.append("FREEZE")
    if not auction_seen:
        codes.append("MISSING_AUCTION")
    return sorted(codes)


# ---- prefix-safe features (FE-0043) ----

def feature_at(closes: list[float], timestamps: list[datetime], t: datetime, window: int = 5) -> float | None:
    """Mean of the last ``window`` closes whose timestamp is <= t. Future bars cannot change the value."""
    idx = [i for i, ts in enumerate(timestamps) if _aware(ts, "bar") <= _aware(t, "t")]
    if len(idx) < window:
        return None
    last = idx[-window:]
    return sum(closes[i] for i in last) / window


# ---- coverage (FE-0044) ----

def coverage(sessions: dict[str, int], names: dict[str, bool], intents: list[tuple[float, bool]],
             min_quotes: int = 1) -> dict[str, float]:
    """Share of sessions with enough quotes, share of names with coverage, and share of intent notional covered."""
    total_sessions = len(sessions)
    good_sessions = sum(1 for n in sessions.values() if n >= min_quotes)
    total_names = len(names)
    good_names = sum(1 for ok in names.values() if ok)
    total_notional = sum(n for n, _ in intents)
    covered = sum(n for n, ok in intents if ok)
    return {
        "sessions_pct": round(100.0 * good_sessions / total_sessions, 4) if total_sessions else 0.0,
        "names_pct": round(100.0 * good_names / total_names, 4) if total_names else 0.0,
        "intent_notional_pct": round(100.0 * covered / total_notional, 4) if total_notional else 0.0,
    }
