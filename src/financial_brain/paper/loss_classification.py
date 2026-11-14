"""Evidence-linked, append-only classification of losing paper trades."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math


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


def classify_loss(
    *, trade_id: str, day: str, net_pnl: float,
    evidence: Sequence[Mapping[str, str]] | None,
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
        normalized[(category, evidence_id)] = {"category": category, "evidence_id": evidence_id}
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


def store_classification(con, result: Mapping[str, object], *, recorded_at_utc: str | None = None) -> bool:
    """Persist once; identical replays are idempotent and changed evidence conflicts."""
    required = {"trade_id", "day", "net_pnl", "primary_category", "categories", "evidence"}
    if set(result) != required:
        raise LossClassificationError("classification result is incomplete")
    try:
        checked = classify_loss(
            trade_id=result["trade_id"], day=result["day"], net_pnl=result["net_pnl"],
            evidence=result["evidence"],
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
