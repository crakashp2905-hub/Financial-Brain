"""Evidence-linked, append-only classification of losing paper trades."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Protocol, cast

from financial_brain.forward_edge.durable import DurableJournal


CATEGORIES = (
    "FALSE_SIGNAL",
    "UNFAVORABLE_REGIME",
    "TIMING",
    "EXECUTION_COST",
    "SIZE",
    "STALE_DATA",
    "INVALID_UNIVERSE",
    "INFRASTRUCTURE",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS loss_classifications (
    trade_id VARCHAR PRIMARY KEY,
    day VARCHAR NOT NULL,
    net_pnl DOUBLE NOT NULL,
    primary_category VARCHAR NOT NULL,
    categories_json VARCHAR NOT NULL,
    evidence_json VARCHAR NOT NULL,
    classification_sha256 VARCHAR NOT NULL,
    recorded_at_utc VARCHAR NOT NULL
)
"""


class LossClassificationError(ValueError):
    """Invalid evidence, classification or conflicting immutable record."""


class LossEvidenceResolver(Protocol):
    def resolve(self, evidence_id: str) -> dict: ...


class LossEvidenceRegistry:
    """Append-only mapping from loss evidence IDs to category and artifact digest."""

    def __init__(self, path: Path) -> None:
        self.journal = DurableJournal(Path(path))
        self._records: dict[str, dict[str, str]] = {}
        for event in self.journal.records():
            if event.get("t") != "LOSS_EVIDENCE_REGISTERED":
                raise LossClassificationError("unknown loss evidence registry event")
            try:
                self._accept(event["record"])
            except (KeyError, TypeError) as exc:
                raise LossClassificationError("malformed loss evidence registry record") from exc

    @staticmethod
    def _validate(record: dict) -> dict[str, str]:
        evidence_id, category, digest = (
            record.get("evidence_id"), record.get("category"), record.get("artifact_sha256")
        )
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise LossClassificationError("evidence_id must be nonempty")
        if category not in CATEGORIES:
            raise LossClassificationError(f"unknown loss category: {category!r}")
        if (not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            raise LossClassificationError("artifact_sha256 must be lowercase SHA-256")
        payload = {"evidence_id": evidence_id, "category": category, "artifact_sha256": digest}
        seal = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"))
                              .encode("utf-8")).hexdigest()
        if record.get("registry_sha256") != seal:
            raise LossClassificationError("loss evidence registry digest mismatch")
        return payload

    def _accept(self, record: dict) -> None:
        payload = self._validate(record)
        evidence_id = payload["evidence_id"]
        prior = self._records.get(evidence_id)
        if prior is not None and prior != payload:
            raise LossClassificationError("registered loss evidence is immutable")
        self._records[evidence_id] = payload

    def register(self, *, evidence_id: str, category: str, artifact_sha256: str) -> dict:
        payload = {"evidence_id": evidence_id, "category": category,
                   "artifact_sha256": artifact_sha256}
        seal = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"))
                              .encode("utf-8")).hexdigest()
        record = {**payload, "registry_sha256": seal}
        checked = self._validate(record)
        prior = self._records.get(evidence_id)
        if prior is not None:
            if prior != checked:
                raise LossClassificationError("registered loss evidence is immutable")
            return {**prior, "registry_sha256": seal}
        self.journal.append({"t": "LOSS_EVIDENCE_REGISTERED", "record": record})
        self._records[evidence_id] = checked
        return dict(record)

    def resolve(self, evidence_id: str) -> dict:
        try:
            return dict(self._records[evidence_id])
        except KeyError as exc:
            raise LossClassificationError("loss evidence ID is not registered") from exc


def classify_loss(
    *, trade_id: str, day: str, net_pnl: float,
    evidence: Sequence[Mapping[str, str]] | None,
    evidence_registry: LossEvidenceResolver | None = None,
) -> dict:
    """Classify a losing trade only into categories carrying evidence identifiers.

    Evidence IDs are preserved verbatim. This function does not validate them
    against an authoritative store, so its output is an attributed review record,
    not a causal finding. No evidence yields the explicit ``UNKNOWN`` category.
    """
    if not isinstance(trade_id, str) or not trade_id.strip():
        raise LossClassificationError("trade_id must be nonempty")
    if not isinstance(day, str) or not day.strip():
        raise LossClassificationError("day must be nonempty")
    if isinstance(net_pnl, bool) or not isinstance(net_pnl, (int, float)) or not math.isfinite(net_pnl):
        raise LossClassificationError("net_pnl must be finite")
    if net_pnl >= 0:
        return {"trade_id": trade_id, "day": day, "net_pnl": float(net_pnl),
                "primary_category": "NOT_A_LOSS", "categories": [], "evidence": []}

    normalized: dict[tuple[str, str], dict[str, str]] = {}
    for item in evidence or ():
        if not isinstance(item, Mapping):
            raise LossClassificationError("each evidence item must be a mapping")
        category, evidence_id = item.get("category"), item.get("evidence_id")
        if category not in CATEGORIES:
            raise LossClassificationError(f"unknown loss category: {category!r}")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise LossClassificationError("evidence_id must be nonempty")
        record = {"category": category, "evidence_id": evidence_id}
        if evidence_registry is not None:
            try:
                registered = evidence_registry.resolve(evidence_id)
            except (KeyError, ValueError) as exc:
                raise LossClassificationError(f"unresolved evidence ID: {evidence_id}") from exc
            if registered.get("category") != category:
                raise LossClassificationError("evidence category differs from registered category")
            record["artifact_sha256"] = str(registered["artifact_sha256"])
        elif "artifact_sha256" in item:
            raise LossClassificationError("artifact evidence requires an evidence registry")
        normalized[(category, evidence_id)] = record
    records = sorted(normalized.values(), key=lambda row: (row["category"], row["evidence_id"]))
    categories = sorted({row["category"] for row in records})
    primary = categories[0] if categories else "UNKNOWN"
    return {"trade_id": trade_id, "day": day, "net_pnl": float(net_pnl),
            "primary_category": primary, "categories": categories, "evidence": records}


def _record_digest(result: Mapping[str, object]) -> str:
    payload = {key: result[key] for key in (
        "trade_id", "day", "net_pnl", "primary_category", "categories", "evidence",
    )}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode("utf-8")).hexdigest()


def store_classification(con, result: Mapping[str, object], *, recorded_at_utc: str | None = None,
                         evidence_registry: LossEvidenceResolver | None = None) -> bool:
    """Persist once; identical replays are idempotent and changed evidence conflicts."""
    required = {"trade_id", "day", "net_pnl", "primary_category", "categories", "evidence"}
    if set(result) != required:
        raise LossClassificationError("classification result is incomplete")
    raw_evidence = cast(Sequence[Mapping[str, str]] | None, result["evidence"])
    if evidence_registry is None and any(
        isinstance(item, Mapping) and "artifact_sha256" in item for item in (raw_evidence or ())
    ):
        raise LossClassificationError("verified evidence requires an evidence registry")
    try:
        checked = classify_loss(
            trade_id=cast(str, result["trade_id"]), day=cast(str, result["day"]),
            net_pnl=cast(float, result["net_pnl"]),
            evidence=raw_evidence, evidence_registry=evidence_registry,
        )
    except (TypeError, ValueError) as exc:
        raise LossClassificationError("classification result is invalid") from exc
    if dict(result) != checked:
        raise LossClassificationError("classification result does not match its evidence")
    digest = _record_digest(result)
    stamp = recorded_at_utc or datetime.now(timezone.utc).isoformat()
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise LossClassificationError("recorded_at_utc must be timezone-aware ISO time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LossClassificationError("recorded_at_utc must be timezone-aware ISO time")
    con.execute(SCHEMA)
    encoded_categories = json.dumps(result["categories"], separators=(",", ":"), allow_nan=False)
    encoded_evidence = json.dumps(result["evidence"], sort_keys=True, separators=(",", ":"), allow_nan=False)
    existing = con.execute(
        "SELECT classification_sha256 FROM loss_classifications WHERE trade_id = ?",
        [result["trade_id"]],
    ).fetchone()
    if existing:
        if existing[0] != digest:
            raise LossClassificationError("conflicting classification for immutable trade_id")
        return False
    con.execute("INSERT INTO loss_classifications VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
        result["trade_id"], result["day"], result["net_pnl"], result["primary_category"],
        encoded_categories, encoded_evidence, digest, parsed.astimezone(timezone.utc).isoformat(),
    ])
    return True
