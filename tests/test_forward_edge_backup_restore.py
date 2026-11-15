import json
import sqlite3

import pytest

from financial_brain.forward_edge.backup_restore import (
    BackupInput,
    BackupRestoreError,
    create_backup,
    restore_drill,
)


def test_backup_restore_drill_preserves_sqlite_and_manifest_bytes(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    db = source / "paper.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE fills (fill_id TEXT PRIMARY KEY, quantity INTEGER)")
        con.execute("INSERT INTO fills VALUES ('f-1', 7)")
    manifest = source / "decision.json"
    manifest.write_text('{"decision_id":"d1","hash":"abc"}\n', encoding="utf-8")

    backup = tmp_path / "backup"
    recorded = create_backup([BackupInput("paper/ledger.sqlite3", db, "sqlite"),
                              BackupInput("evidence/decision.json", manifest, "file")], backup)
    restored = tmp_path / "restore"
    result = restore_drill(backup, restored)
    assert result["manifest_sha256"] == recorded["manifest_sha256"]
    with sqlite3.connect(restored / "paper/ledger.sqlite3") as con:
        assert con.execute("SELECT * FROM fills").fetchall() == [("f-1", 7)]
    assert (restored / "evidence/decision.json").read_bytes() == manifest.read_bytes()


def test_backup_rejects_overwrite_and_restore_detects_tampering(tmp_path) -> None:
    src = tmp_path / "data.bin"
    src.write_bytes(b"original")
    backup = tmp_path / "backup"
    create_backup([BackupInput("data.bin", src, "file")], backup)
    with pytest.raises(BackupRestoreError, match="already exist"):
        create_backup([BackupInput("data.bin", src, "file")], backup)
    (backup / "data.bin").write_bytes(b"changed")
    with pytest.raises(BackupRestoreError, match="hash mismatch"):
        restore_drill(backup, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


def test_restore_rejects_manifest_path_traversal_and_hash_edit(tmp_path) -> None:
    src = tmp_path / "data.bin"
    src.write_bytes(b"safe")
    backup = tmp_path / "backup"
    create_backup([BackupInput("data.bin", src, "file")], backup)
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["entries"][0]["name"] = "../escape"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BackupRestoreError, match="manifest hash mismatch"):
        restore_drill(backup, tmp_path / "restore")
