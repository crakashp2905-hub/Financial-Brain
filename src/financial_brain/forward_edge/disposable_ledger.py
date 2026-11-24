"""Isolated, reproducible DuckDB ledgers for tests and local experiments.

The helper only creates a new database below a caller-provided temporary root. It
refuses repository paths and existing files, so it cannot overwrite research or
portfolio records. It never stores credentials or connects to external services.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb


class DisposableLedgerError(ValueError):
    """The requested path is not a safe disposable ledger location."""


@dataclass
class DisposableLedger:
    path: Path

    def __enter__(self) -> "DisposableLedger":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.path.unlink(missing_ok=True)
        self.path.with_suffix(self.path.suffix + ".wal").unlink(missing_ok=True)

    def connect(self) -> duckdb.DuckDBPyConnection:
        if not self.path.is_file():
            raise DisposableLedgerError("disposable ledger is closed or missing")
        return duckdb.connect(str(self.path))

    def append(self, event_id: str, event: dict[str, Any]) -> bool:
        """Insert an immutable event; exact duplicate IDs are idempotent."""
        if not event_id or not event_id.strip():
            raise DisposableLedgerError("event_id is required")
        payload = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        con = self.connect()
        try:
            existing = con.execute("SELECT payload_sha256 FROM events WHERE event_id = ?", [event_id]).fetchone()
            if existing is not None:
                if existing[0] != digest:
                    raise DisposableLedgerError("event_id already exists with different content")
                return False
            con.execute("INSERT INTO events VALUES (?, ?, ?)", [event_id, payload, digest])
            return True
        finally:
            con.close()


def create_disposable_ledger(root: Path, *, repo_root: Path) -> DisposableLedger:
    """Create a new ledger under an isolated temporary directory.

    The caller owns cleanup through the returned context manager. `root` must be
    outside the repository and must not already contain the deterministic filename.
    """
    root = Path(root).resolve()
    repo = Path(repo_root).resolve()
    if root == repo or repo in root.parents or root in repo.parents:
        raise DisposableLedgerError("temporary ledger root must not overlap the repository")
    if not root.is_dir():
        raise DisposableLedgerError("temporary ledger root must already exist")
    path = root / "financial-brain-disposable-ledger.duckdb"
    if path.exists():
        raise DisposableLedgerError("refusing to overwrite an existing ledger")
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE events (event_id VARCHAR PRIMARY KEY, payload JSON NOT NULL, payload_sha256 VARCHAR NOT NULL)")
        con.execute("CREATE TABLE ledger_meta (key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL)")
        con.execute("INSERT INTO ledger_meta VALUES ('schema', 'fb.disposable-ledger.v1')")
    except Exception:
        con.close()
        path.unlink(missing_ok=True)
        raise
    con.close()
    return DisposableLedger(path)


def temporary_disposable_ledger(*, repo_root: Path) -> DisposableLedger:
    """Create a disposable ledger in a fresh OS temporary directory."""
    parent = Path(tempfile.mkdtemp(prefix="fb-ledger-"))
    return create_disposable_ledger(parent, repo_root=repo_root)
