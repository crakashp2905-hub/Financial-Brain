"""Publish and verify one bounded research snapshot before any agent consumes it.

The manifest is written last. A reader seeing a mixed old/new pair rejects it by hash.
Local hashes detect inconsistency, not malicious modification or independent time of creation.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

SCHEMA = "financial-brain.snapshot.v1"


class SnapshotError(ValueError):
    pass


def manifest_path(path: Path) -> Path:
    return path.with_name(path.stem + ".manifest.json")


def _json_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _replace_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def publish(path: Path, rows: list[dict], *, asof: date | str) -> dict:
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise SnapshotError("snapshot must be a list of objects")
    day = date.fromisoformat(str(asof))
    if any(str(r.get("timestamp", ""))[:10] != day.isoformat() for r in rows):
        raise SnapshotError("every opportunity must share the declared as-of date")
    body = _json_bytes(rows)
    manifest = {"schema": SCHEMA, "asof": day.isoformat(), "rows": len(rows),
                "sha256": hashlib.sha256(body).hexdigest(),
                "published_at": datetime.now(timezone.utc).isoformat(),
                "producer": "paper.terminal"}
    _replace_bytes(path, body)
    _replace_bytes(manifest_path(path), _json_bytes(manifest))
    return manifest


def read(path: Path) -> tuple[list[dict], dict]:
    try:
        manifest = json.loads(manifest_path(path).read_text(encoding="utf-8"))
        body = path.read_bytes()
        rows = json.loads(body)
    except (OSError, ValueError) as exc:
        raise SnapshotError("verified opportunity snapshot is missing or unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise SnapshotError("snapshot manifest schema is not supported")
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise SnapshotError("snapshot is not a list of objects")
    if (hashlib.sha256(body).hexdigest() != manifest.get("sha256")
            or len(rows) != manifest.get("rows")
            or any(str(r.get("timestamp", ""))[:10] != manifest.get("asof") for r in rows)):
        raise SnapshotError("snapshot and manifest disagree; refusing mixed or modified data")
    return rows, manifest
