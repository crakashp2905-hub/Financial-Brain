"""Durable write-ahead journal, outbox and crash recovery (FE-0011, FE-0012, FE-0013, FE-0020, FE-0021, FE-0049, FE-0072).

* Every record is appended as one line {seq, prev, hash, rec}, flushed and fsynced. Sequence numbers are monotonic and
  gap-free; the hash chain makes any edit, drop or reorder detectable.
* On open, a torn final line (power loss mid-write) is discarded; corruption anywhere else raises ``JournalCorrupt``.
* The outbox writes PENDING before the external action. After a crash, any key that is not ACKED is UNKNOWN: it is
  reconciled against the venue, never blindly resent and never assumed filled.
* ``rollover`` writes a snapshot of folded state into a fresh segment atomically (temp file + replace); the state hash
  is identical before and after, and a backup restores to the same hash.
"""
from __future__ import annotations

import hashlib
import json
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
        digest = _link(self.head, rec)
        line = _canon({"seq": self.seq + 1, "prev": self.head, "hash": digest, "rec": rec}) + "\n"
        with self.path.open("ab") as fh:
            fh.write(line.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        self.seq += 1
        self.head = digest
        return self.seq

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


# ---- state folded from the journal ----

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


# ---- simulated venue ----

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


# ---- rollover, backup, restore (FE-0021) ----

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
