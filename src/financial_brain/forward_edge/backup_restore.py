"""Hash-verified backup and isolated restore drill for forward-edge evidence (FE-0077)."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal


class BackupRestoreError(ValueError):
    """Backup input, manifest or restore target is unsafe or inconsistent."""


@dataclass(frozen=True)
class BackupInput:
    name: str
    path: Path
    kind: Literal["file", "sqlite"]


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _safe_name(name: str) -> PurePosixPath:
    value = PurePosixPath(name)
    if not name.strip() or value.is_absolute() or any(part in {"", ".", ".."} for part in value.parts):
        raise BackupRestoreError("backup names must be safe relative POSIX paths")
    return value


def _sqlite_integrity(path: Path) -> None:
    try:
        with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as con:
            row = con.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise BackupRestoreError("SQLite database could not be opened for integrity check") from exc
    if not row or row[0] != "ok":
        raise BackupRestoreError(f"SQLite integrity check failed: {row[0] if row else 'no result'}")


def create_backup(inputs: list[BackupInput], destination: Path) -> dict[str, object]:
    """Create a content-hashed bundle, publishing its completion manifest last."""
    destination = Path(destination).resolve()
    if destination.exists():
        raise BackupRestoreError("backup destination must not already exist")
    if not inputs:
        raise BackupRestoreError("at least one input is required")
    names = [_safe_name(item.name) for item in inputs]
    if len(set(names)) != len(names):
        raise BackupRestoreError("backup names must be unique")
    source_paths = [Path(item.path).resolve(strict=True) for item in inputs]
    if len(set(source_paths)) != len(source_paths):
        raise BackupRestoreError("source paths must be unique")
    for original, resolved in zip((Path(item.path) for item in inputs), source_paths, strict=True):
        if original.is_symlink() or not resolved.is_file():
            raise BackupRestoreError("backup sources must be regular files, not symlinks")
        if destination == resolved or destination in resolved.parents or resolved in destination.parents:
            raise BackupRestoreError("backup sources and destination must not overlap")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    published_destination = False
    try:
        entries: list[dict[str, object]] = []
        for item, rel in zip(inputs, names, strict=True):
            src = Path(item.path).resolve(strict=True)
            if item.kind not in {"file", "sqlite"}:
                raise BackupRestoreError("unsupported backup kind")
            if item.kind == "sqlite":
                _sqlite_integrity(src)
            target = staging.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if item.kind == "sqlite":
                with sqlite3.connect(str(src)) as source_con, sqlite3.connect(str(target)) as target_con:
                    source_con.backup(target_con)
                _sqlite_integrity(target)
            else:
                shutil.copyfile(src, target)
            entries.append({"name": rel.as_posix(), "kind": item.kind,
                            "sha256": _digest(target), "size_bytes": target.stat().st_size})
        manifest: dict[str, object] = {"schema": "fb.forward-edge.backup.v1", "entries": entries}
        manifest["manifest_sha256"] = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        (staging / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        # Publish the manifest last: a crash during copy leaves no valid
        # completion record, so readers will refuse the incomplete directory.
        destination.mkdir()
        published_destination = True
        for rel in names:
            published = destination.joinpath(*rel.parts)
            published.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(staging.joinpath(*rel.parts), published)
        shutil.copyfile(staging / "manifest.json", destination / "manifest.json")
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if published_destination and destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def restore_drill(backup_dir: Path, destination: Path) -> dict[str, object]:
    """Verify every artifact, then restore into a newly created isolated directory."""
    backup_dir = Path(backup_dir).resolve(strict=True)
    destination = Path(destination).resolve()
    if not backup_dir.is_dir() or destination.exists():
        raise BackupRestoreError("backup must be a directory and restore destination must not exist")
    manifest_path = backup_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupRestoreError("backup manifest is missing or invalid") from exc
    if manifest.get("schema") != "fb.forward-edge.backup.v1" or not isinstance(manifest.get("entries"), list):
        raise BackupRestoreError("unsupported backup manifest")
    claimed_hash = manifest.get("manifest_sha256")
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    actual_hash = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if claimed_hash != actual_hash:
        raise BackupRestoreError("backup manifest hash mismatch")
    verified: list[tuple[dict[str, object], Path]] = []
    seen: set[PurePosixPath] = set()
    for entry in manifest["entries"]:
        if not isinstance(entry, dict):
            raise BackupRestoreError("invalid backup entry")
        rel = _safe_name(str(entry.get("name", "")))
        if rel.as_posix() == "manifest.json" or rel in seen:
            raise BackupRestoreError("duplicate or reserved backup path")
        seen.add(rel)
        source = backup_dir.joinpath(*rel.parts)
        if source.is_symlink() or not source.is_file() or _digest(source) != entry.get("sha256"):
            raise BackupRestoreError(f"backup content hash mismatch: {rel.as_posix()}")
        if source.stat().st_size != entry.get("size_bytes"):
            raise BackupRestoreError(f"backup size mismatch: {rel.as_posix()}")
        kind = entry.get("kind")
        if kind not in {"file", "sqlite"}:
            raise BackupRestoreError("unsupported backup entry kind")
        if kind == "sqlite":
            _sqlite_integrity(source)
        verified.append((entry, source))
    if not verified:
        raise BackupRestoreError("backup manifest has no artifacts")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.restore-", dir=destination.parent))
    published_destination = False
    try:
        for entry, source in verified:
            target = staging.joinpath(*_safe_name(str(entry["name"])).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if _digest(target) != entry["sha256"]:
                raise BackupRestoreError("restored content hash mismatch")
            if entry["kind"] == "sqlite":
                _sqlite_integrity(target)
        shutil.copyfile(manifest_path, staging / "manifest.json")
        destination.mkdir()
        published_destination = True
        for entry, _ in verified:
            rel = _safe_name(str(entry["name"]))
            published = destination.joinpath(*rel.parts)
            published.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(staging.joinpath(*rel.parts), published)
        shutil.copyfile(staging / "manifest.json", destination / "manifest.json")
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if published_destination and destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {"manifest_sha256": claimed_hash,
            "restored": sorted(str(entry["name"]) for entry, _ in verified),
            "destination": str(destination)}
