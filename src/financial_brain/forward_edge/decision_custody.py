"""Forward decision custody (FE-0045 .. FE-0054): schedules, manifests, pre-entry recording, session denominators, bundles.

Builds on ``custody.py`` (receipt classification) and ``market_data.py`` (snapshot hashes). No provider is contacted;
with no independent receipts configured every decision is LOCAL_ONLY, which is never a formal claim.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from financial_brain.forward_edge.custody import (CustodyStatus, ProviderNotConfigured, Receipt, TimestampProvider,
                                                  TrustRoot, classify_custody)
from financial_brain.forward_edge.durable import DurableJournal

_SHA = re.compile(r"^[0-9a-f]{64}$")


class CustodyError(ValueError):
    pass


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CustodyError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


# ---- FE-0045 frozen, nonoverlapping cohort schedule ----

@dataclass(frozen=True)
class Cohort:
    cohort_id: str
    start: str  # ISO date, inclusive
    end: str    # ISO date, inclusive


@dataclass(frozen=True)
class CohortSchedule:
    charter_sha256: str
    cohorts: tuple[Cohort, ...]

    def __post_init__(self) -> None:
        if not _SHA.match(self.charter_sha256):
            raise CustodyError("schedule must name its charter hash")
        ordered = sorted(self.cohorts, key=lambda c: c.start)
        if tuple(ordered) != self.cohorts:
            raise CustodyError("cohorts must be sorted by start date")
        for a, b in zip(self.cohorts, self.cohorts[1:]):
            if b.start <= a.end:
                raise CustodyError(f"cohorts {a.cohort_id} and {b.cohort_id} overlap")
        if len({c.cohort_id for c in self.cohorts}) != len(self.cohorts):
            raise CustodyError("cohort ids must be unique")

    def sha256(self) -> str:
        return hashlib.sha256(_canon({"charter": self.charter_sha256,
                                      "cohorts": [[c.cohort_id, c.start, c.end] for c in self.cohorts]})).hexdigest()


def assert_schedule_unchanged(schedule: CohortSchedule, pinned_sha256: str, *, observations_started: bool) -> None:
    if schedule.sha256() != pinned_sha256:
        if observations_started:
            raise CustodyError("schedule changed after observations began: a new prospective clock is required")
        raise CustodyError("schedule differs from its pin")


# ---- FE-0046 canonical decision manifest ----

MANIFEST_FIELDS = ("session_id", "strategy_version_sha256", "snapshot_sha256", "decision_at_utc",
                   "entry_earliest_at_utc", "side", "instrument_id", "intended_quantity", "risk_policy_sha256",
                   "benchmark_id", "evidence_tier")
SIDES = frozenset({"BUY", "SELL", "NONE"})
TIERS = frozenset({"SHADOW", "LOCAL_PAPER", "INDEPENDENT_FORWARD"})


@dataclass(frozen=True)
class DecisionManifest:
    session_id: str
    strategy_version_sha256: str
    snapshot_sha256: str
    decision_at_utc: str
    entry_earliest_at_utc: str
    side: str
    instrument_id: str
    intended_quantity: int
    risk_policy_sha256: str
    benchmark_id: str
    evidence_tier: str

    def __post_init__(self) -> None:
        for name in ("strategy_version_sha256", "snapshot_sha256", "risk_policy_sha256"):
            if not _SHA.match(getattr(self, name)):
                raise CustodyError(f"{name} must be sha256 hex")
        if self.side not in SIDES:
            raise CustodyError("side must be BUY, SELL or NONE")
        if self.evidence_tier not in TIERS:
            raise CustodyError("unknown evidence tier")
        if type(self.intended_quantity) is not int or self.intended_quantity < 0:
            raise CustodyError("quantity must be a non-negative integer (no floats in manifests)")
        d = _utc(datetime.fromisoformat(self.decision_at_utc), "decision")
        e = _utc(datetime.fromisoformat(self.entry_earliest_at_utc), "entry")
        if d >= e:
            raise CustodyError("decision must precede the earliest entry time")

    def canonical(self) -> bytes:
        return _canon({k: getattr(self, k) for k in MANIFEST_FIELDS})

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical()).hexdigest()

    @property
    def decision_utc(self) -> datetime:
        return _utc(datetime.fromisoformat(self.decision_at_utc), "decision")

    @property
    def entry_utc(self) -> datetime:
        return _utc(datetime.fromisoformat(self.entry_earliest_at_utc), "entry")


# ---- FE-0048 record before entry, FE-0049 persist every session outcome ----

SESSION_OUTCOMES = frozenset({"SUBMITTED", "WITHHELD", "ABSTAINED", "NO_SIGNAL", "NO_TRADE", "REJECTED",
                              "UNKNOWN_ORDER", "UNSUCCESSFUL"})


class ManifestJournal:
    """Append-only. Optionally persists manifests and outcomes in the hash-chained durable journal."""

    def __init__(self, path: Path | None = None) -> None:
        self._durable = DurableJournal(path) if path is not None else None
        self._manifests: dict[str, DecisionManifest] = {}
        self._recorded_at: dict[str, datetime] = {}
        self._receipts: dict[str, Receipt] = {}
        self._sessions: dict[str, str] = {}
        if self._durable is not None:
            self._recover()

    def _recover(self) -> None:
        assert self._durable is not None
        for row in self._durable.records():
            if row.get("t") == "DECISION_MANIFEST":
                try:
                    manifest = DecisionManifest(**row["manifest"])
                    recorded = _utc(datetime.fromisoformat(row["recorded_at_utc"]), "recorded_at")
                except (KeyError, TypeError, ValueError) as exc:
                    raise CustodyError("malformed durable decision manifest") from exc
                if manifest.sha256() != row.get("manifest_sha256"):
                    raise CustodyError("durable decision manifest hash mismatch")
                if recorded < manifest.decision_utc or recorded >= manifest.entry_utc:
                    raise CustodyError("durable manifest timestamp violates its decision/entry window")
                if manifest.session_id in self._manifests:
                    raise CustodyError("duplicate durable decision manifest")
                self._manifests[manifest.session_id] = manifest
                self._recorded_at[manifest.session_id] = recorded
            elif row.get("t") == "SESSION_OUTCOME":
                session_id, outcome = row.get("session_id"), row.get("outcome")
                if not isinstance(session_id, str) or not session_id or outcome not in SESSION_OUTCOMES:
                    raise CustodyError("malformed durable session outcome")
                if session_id in self._sessions:
                    raise CustodyError("duplicate durable session outcome")
                self._sessions[session_id] = outcome
            elif row.get("t") == "DECISION_RECEIPT":
                session_id = row.get("session_id")
                if session_id not in self._manifests or session_id in self._receipts:
                    raise CustodyError("durable receipt has no unique prior manifest")
                try:
                    receipt = Receipt(row["provider_id"], row["key_id"], row["digest_sha256"],
                                      datetime.fromisoformat(row["anchored_at_utc"]), row["signature_b64"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise CustodyError("malformed durable decision receipt") from exc
                self._receipts[session_id] = receipt
            else:
                raise CustodyError("unknown durable manifest journal event")

    def record(self, manifest: DecisionManifest, *, recorded_at_utc: datetime) -> str:
        recorded = _utc(recorded_at_utc, "recorded_at")
        if manifest.session_id in self._manifests:
            raise CustodyError("session already recorded: overwrite is forbidden")
        if recorded >= manifest.entry_utc:
            raise CustodyError("manifest recorded at or after its earliest entry time")
        if recorded < manifest.decision_utc:
            raise CustodyError("manifest recorded before its decision time")
        if self._durable is not None:
            self._durable.append({"t": "DECISION_MANIFEST", "session_id": manifest.session_id,
                                  "manifest": json.loads(manifest.canonical().decode("utf-8")),
                                  "manifest_sha256": manifest.sha256(), "recorded_at_utc": recorded.isoformat()})
        self._manifests[manifest.session_id] = manifest
        self._recorded_at[manifest.session_id] = recorded
        return manifest.sha256()

    def attach_receipt(self, session_id: str, receipt: Receipt) -> None:
        if session_id not in self._manifests:
            raise CustodyError("no manifest for session")
        if session_id in self._receipts:
            raise CustodyError("receipt already attached")
        if self._durable is not None:
            self._durable.append({"t": "DECISION_RECEIPT", "session_id": session_id,
                                  "provider_id": receipt.provider_id, "key_id": receipt.key_id,
                                  "digest_sha256": receipt.digest_sha256,
                                  "anchored_at_utc": _utc(receipt.anchored_at_utc, "receipt").isoformat(),
                                  "signature_b64": receipt.signature_b64})
        self._receipts[session_id] = receipt

    def anchor(self, session_id: str, provider: TimestampProvider,
               trust_roots: dict[tuple[str, str], TrustRoot]) -> CustodyStatus:
        """Request an optional signed timestamp; an unconfigured provider leaves LOCAL_ONLY evidence."""
        if session_id not in self._manifests:
            raise CustodyError("no manifest for session")
        try:
            receipt = provider.anchor(self._manifests[session_id].sha256())
        except ProviderNotConfigured:
            return self.status(session_id, trust_roots)
        self.attach_receipt(session_id, receipt)
        return self.status(session_id, trust_roots)

    def outcome(self, session_id: str, outcome: str) -> None:
        if outcome not in SESSION_OUTCOMES:
            raise CustodyError("unknown session outcome")
        if session_id in self._sessions:
            raise CustodyError("session outcome already recorded")
        if self._durable is not None:
            self._durable.append({"t": "SESSION_OUTCOME", "session_id": session_id, "outcome": outcome})
        self._sessions[session_id] = outcome

    def status(self, session_id: str, trust_roots: dict[tuple[str, str], TrustRoot]) -> CustodyStatus:
        m = self._manifests[session_id]
        return classify_custody(manifest_sha256=m.sha256(), decision_at_utc=m.decision_utc,
                                entry_earliest_at_utc=m.entry_utc, receipt=self._receipts.get(session_id),
                                trust_roots=trust_roots)

    def denominator(self, schedule_sessions: list[str]) -> dict[str, int]:
        """Every scheduled session counts, including zero-signal and unsuccessful ones."""
        counts: dict[str, int] = {o: 0 for o in sorted(SESSION_OUTCOMES)}
        counts["UNRECORDED"] = 0
        for sid in schedule_sessions:
            counts[self._sessions.get(sid, "UNRECORDED")] += 1
        counts["SCHEDULED"] = len(schedule_sessions)
        return counts

    def verified_session_ids(self, trust_roots: dict[tuple[str, str], TrustRoot]) -> set[str]:
        """Only sessions whose custody is VERIFIED count toward a formal forward claim (FE-0051 analogue)."""
        return {sid for sid in self._manifests
                if self.status(sid, trust_roots) is CustodyStatus.VERIFIED}

    def manifests(self) -> list[DecisionManifest]:
        return [self._manifests[k] for k in sorted(self._manifests)]


# ---- FE-0054 attestation bundle export and offline verification ----

def export_bundle(journal: ManifestJournal, schedule: CohortSchedule, out: Path,
                  *, sessions: list[str]) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    rows = [json.loads(m.canonical().decode("utf-8")) | {"manifest_sha256": m.sha256(),
                                                          "recorded_at_utc": journal._recorded_at[m.session_id].isoformat()}
            for m in journal.manifests()]
    files["manifests.jsonl"] = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
    files["schedule.json"] = json.dumps({"charter_sha256": schedule.charter_sha256,
                                         "cohorts": [[c.cohort_id, c.start, c.end] for c in schedule.cohorts],
                                         "schedule_sha256": schedule.sha256()}, sort_keys=True) + "\n"
    files["denominator.json"] = json.dumps(journal.denominator(sessions), sort_keys=True) + "\n"
    index = {}
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8", newline="\n")
        index[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    (out / "index.json").write_text(json.dumps(index, sort_keys=True, indent=1) + "\n", encoding="utf-8", newline="\n")
    return index


def verify_bundle(bundle: Path) -> list[str]:
    """Recompute every file digest and every manifest digest from the bytes on disk. Returns problems (empty = ok)."""
    problems = []
    index = json.loads((bundle / "index.json").read_text(encoding="utf-8"))
    for name, expected in sorted(index.items()):
        path = bundle / name
        if not path.exists():
            problems.append(f"missing {name}")
            continue
        text = path.read_text(encoding="utf-8")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != expected:
            problems.append(f"digest mismatch {name}")
    manifests = bundle / "manifests.jsonl"
    if manifests.exists():
        for line in manifests.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            digest = row.pop("manifest_sha256", None)
            row.pop("recorded_at_utc", None)
            if hashlib.sha256(_canon(row)).hexdigest() != digest:
                problems.append(f"manifest digest mismatch {row.get('session_id')}")
    return problems
