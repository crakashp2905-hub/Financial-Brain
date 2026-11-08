"""Operations: atomic writes, cache keys, idempotent recording, redacted rotating logs, limits and schema loading.

Stage 24 reliability (CP-0366..0373, CP-0377). Nothing here touches protected records.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import tempfile
from dataclasses import fields
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from financial_brain.patterns.agent import redact
from financial_brain.patterns.events_store import EventLog
from financial_brain.patterns.spec import PatternEvent

MAX_BARS = 20_000
MAX_SYMBOLS = 2_000
SCHEMA_FIELDS = frozenset(f.name for f in fields(PatternEvent))


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temporary file in the same folder, then replace (CP-0366). UTF-8 always."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


class ResultCache:
    """Cache entries are valid only for the exact input manifest hash (CP-0368)."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def get(self, manifest_sha: str, key: str) -> Any | None:
        return self._store.get(f"{manifest_sha}:{key}")

    def put(self, manifest_sha: str, key: str, value: Any) -> None:
        self._store[f"{manifest_sha}:{key}"] = value

    def invalidate_except(self, manifest_sha: str) -> None:
        self._store = {k: v for k, v in self._store.items() if k.startswith(manifest_sha + ":")}


def record_idempotently(log: EventLog, events: list[PatternEvent]) -> int:
    """Re-running a scan appends nothing new for the same events (CP-0367). Returns appended count."""
    appended = 0
    for event in events:
        if log.append(event):
            appended += 1
    return appended


def configure_log(path: Path, *, max_bytes: int = 1_000_000, backups: int = 3) -> logging.Logger:
    """Rotating file log with redaction on every record (CP-0369)."""
    logger = logging.getLogger(f"patterns.{path.name}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")

    class _Redact(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.msg = redact(str(record.msg))
            return True

    handler.addFilter(_Redact())
    logger.addHandler(handler)
    return logger


def model_output_or_none(fn: Any, *args: Any, **kwargs: Any) -> tuple[Any | None, str | None]:
    """An unavailable model returns (None, error) and never changes event or fill state (CP-0310)."""
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:  # any model failure is contained here
        return None, type(exc).__name__


def enforce_limits(*, bars: int, symbols: int) -> None:
    """Resource ceilings for small local machines (CP-0371)."""
    if bars > MAX_BARS:
        raise ValueError(f"bars {bars} exceeds the local limit {MAX_BARS}")
    if symbols > MAX_SYMBOLS:
        raise ValueError(f"symbols {symbols} exceeds the local limit {MAX_SYMBOLS}")


def load_event(data: dict[str, Any]) -> PatternEvent:
    """Read a stored event. Older records load with optional fields defaulted; newer unknown fields are refused (CP-0129, CP-0377)."""
    unknown = sorted(set(data) - SCHEMA_FIELDS)
    if unknown:
        raise ValueError(f"record has fields this version does not know: {unknown}; refusing rather than dropping them")
    required = {"event_id", "spec_sha256", "lineage", "timeframe", "candidate_known_at_utc", "status", "data_sha256"}
    missing = sorted(required - set(data))
    if missing:
        raise ValueError(f"record missing required fields: {missing}")
    return PatternEvent.from_dict({**{"decision_at_utc": None}, **data})


# ---------------------------------------------------------------- resumable scans and traces (CP-0131, CP-0133)
class CheckpointConflict(RuntimeError):
    """A checkpoint belongs to another manifest, or would move backwards."""


class ScanCheckpoint:
    """Records the last bar whose events were fully committed, so an interrupted scan resumes there (CP-0131)."""

    def __init__(self, path: Path, manifest_sha256: str) -> None:
        self._path = Path(path)
        self._manifest = manifest_sha256

    def last_committed(self) -> int:
        """Index of the last committed bar, or ``-1`` when nothing is committed yet."""
        if not self._path.exists():
            return -1
        data = json.loads(self._path.read_text(encoding="utf-8"))
        if data.get("manifest") != self._manifest:
            raise CheckpointConflict("checkpoint was written for a different manifest")
        return int(data["last_committed"])

    def commit(self, index: int) -> None:
        current = self.last_committed()
        if index < current:
            raise CheckpointConflict(f"refusing to move the checkpoint back from {current} to {index}")
        atomic_write_text(
            self._path,
            json.dumps({"manifest": self._manifest, "last_committed": index}, sort_keys=True),
        )


def run_resumable(total: int, checkpoint: ScanCheckpoint, process: Any) -> list[Any]:
    """Process bars ``0..total-1`` in order, committing after each bar; a restart resumes after the last commit.

    ``process(index)`` returns that bar's output. The function returns outputs for bars processed in this call.
    """
    outputs: list[Any] = []
    start = checkpoint.last_committed() + 1
    for index in range(start, total):
        outputs.append(process(index))
        checkpoint.commit(index)
    return outputs


def detection_trace(detector: Any, formation_geometry: dict[str, Any], *, detector_name: str, version: str) -> dict[str, Any]:
    """The thresholds a detector ran with, next to what it found (CP-0133)."""
    thresholds = {
        name: param.default
        for name, param in inspect.signature(detector).parameters.items()
        if param.default is not inspect.Parameter.empty
    }
    return {
        "detector": detector_name,
        "version": version,
        "thresholds": {k: v for k, v in sorted(thresholds.items())},
        "geometry": dict(sorted(formation_geometry.items())),
    }

