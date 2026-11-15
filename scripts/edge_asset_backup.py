"""Snapshot local paper databases, market archives, and strategy manifests before migration.

DuckDB files with a sibling WAL are refused: a raw copy can omit committed-but-uncheckpointed
state. Backups stay local and must not be committed because they may contain licensed data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class BackupError(ValueError):
    """A snapshot or restore check could not be completed safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_snapshot(repo_root: Path, backup_root: Path, sources: list[str]) -> dict[str, Any]:
    """Copy stable files and write a manifest; never checkpoint or modify a source database."""
    root = repo_root.resolve()
    destination = backup_root.resolve()
    if destination == root or root not in destination.parents:
        raise BackupError("backup directory must be inside, but separate from, the repository root")
    destination.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for source_name in sources:
        source = (root / source_name).resolve()
        if root not in source.parents:
            raise BackupError(f"source path escapes repository root: {source_name}")
        rel = source.relative_to(root).as_posix()
        entry: dict[str, Any] = {"source": rel, "status": "NOT_FOUND"}
        if not source.is_file():
            entries.append(entry)
            continue
        if source.suffix == ".duckdb" and Path(f"{source}.wal").exists():
            entry.update(status="BLOCKED_ACTIVE_WAL", reason="DuckDB WAL exists; source may be active")
            entries.append(entry)
            continue

        before = source.stat()
        backup_rel = Path("files") / Path(rel)
        backup = destination / backup_rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        partial = backup.with_name(f"{backup.name}.partial")
        partial.unlink(missing_ok=True)
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as reader, partial.open("wb") as writer:
            for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                writer.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        after = source.stat()
        source_stable = (before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns
                         and size == before.st_size)
        source_hash = digest.hexdigest()
        backup_hash = _sha256(partial)
        wal_path = Path(f"{source}.wal") if source.suffix == ".duckdb" else None
        if wal_path is not None and wal_path.exists():
            partial.unlink(missing_ok=True)
            entry.update(status="BLOCKED_ACTIVE_WAL",
                         reason="DuckDB WAL appeared during snapshot; source may be active")
            entries.append(entry)
            continue
        if not source_stable or source_hash != backup_hash:
            partial.unlink(missing_ok=True)
            entry.update(status="BLOCKED_SOURCE_CHANGED", reason="source changed during snapshot")
        else:
            partial.replace(backup)
            entry.update(status="BACKED_UP", bytes=size, sha256=source_hash,
                         backup=backup_rel.as_posix(), source_mtime_ns=before.st_mtime_ns)
        entries.append(entry)

    manifest = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_root": root.name,
        "entries": entries,
    }
    manifest_path = destination / "manifest.json"
    manifest_partial = manifest_path.with_name(f"{manifest_path.name}.partial")
    with manifest_partial.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(manifest, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    manifest_partial.replace(manifest_path)
    return manifest


def verify_restore(backup_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Restore each successful entry under a separate directory and compare byte checksums."""
    root = backup_root.resolve()
    restore_root = root / "restore-check"
    outcomes = []
    for entry in manifest["entries"]:
        if entry["status"] != "BACKED_UP":
            outcomes.append({"source": entry["source"], "status": entry["status"]})
            continue
        backup = (root / entry["backup"]).resolve()
        if root not in backup.parents or not backup.is_file() or _sha256(backup) != entry["sha256"]:
            raise BackupError(f"backup checksum mismatch: {entry['source']}")
        restored = (restore_root / entry["source"]).resolve()
        if restore_root not in restored.parents:
            raise BackupError(f"restore path escapes destination: {entry['source']}")
        restored.parent.mkdir(parents=True, exist_ok=True)
        restored_partial = restored.with_name(f"{restored.name}.partial")
        shutil.copyfile(backup, restored_partial)
        restored_hash = _sha256(restored_partial)
        if restored_hash != entry["sha256"]:
            restored_partial.unlink(missing_ok=True)
            raise BackupError(f"restored checksum mismatch: {entry['source']}")
        restored_partial.replace(restored)
        outcome = {"source": entry["source"], "status": "RESTORED_AND_VERIFIED",
                   "sha256": restored_hash}
        if restored.suffix == ".duckdb":
            import duckdb

            con = duckdb.connect(str(restored), read_only=True)
            try:
                con.execute("SELECT 1").fetchone()
            finally:
                con.close()
            outcome["database_opened_read_only"] = True
        outcomes.append(outcome)
    report = {"schema_version": "1.0", "outcomes": outcomes}
    report_path = root / "restore-check.json"
    report_partial = report_path.with_name(f"{report_path.name}.partial")
    with report_partial.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    report_partial.replace(report_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--source", action="append", required=True,
                        help="repository-relative source path; repeat for each artifact")
    parser.add_argument("--restore-check", action="store_true")
    args = parser.parse_args()
    manifest = create_snapshot(args.repo_root, args.backup_root, args.source)
    if args.restore_check:
        verify_restore(args.backup_root, manifest)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
