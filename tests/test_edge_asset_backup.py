import hashlib
from pathlib import Path

import duckdb
import pytest

from scripts.edge_asset_backup import BackupError, create_snapshot, verify_restore


def test_duckdb_snapshot_restores_and_matches_checksum(tmp_path):
    repo = tmp_path / "repo"
    source = repo / "data" / "paper.duckdb"
    source.parent.mkdir(parents=True)
    con = duckdb.connect(str(source))
    con.execute("CREATE TABLE fills(symbol VARCHAR, quantity INTEGER)")
    con.execute("INSERT INTO fills VALUES ('NSE:ABC', 2), ('NSE:XYZ', 1)")
    con.close()
    expected = hashlib.sha256(source.read_bytes()).hexdigest()

    manifest = create_snapshot(repo, repo / "data" / "backup", ["data/paper.duckdb"])
    report = verify_restore(repo / "data" / "backup", manifest)

    assert manifest["entries"][0]["status"] == "BACKED_UP"
    assert manifest["entries"][0]["sha256"] == expected
    assert report["outcomes"][0]["status"] == "RESTORED_AND_VERIFIED"
    assert report["outcomes"][0]["database_opened_read_only"] is True
    restored = duckdb.connect(str(repo / "data" / "backup" / "restore-check" / "data" / "paper.duckdb"),
                              read_only=True)
    try:
        assert restored.execute("SELECT * FROM fills ORDER BY symbol").fetchall() == [
            ("NSE:ABC", 2), ("NSE:XYZ", 1)
        ]
    finally:
        restored.close()


def test_active_duckdb_wal_is_recorded_as_blocked_without_copy(tmp_path):
    repo = tmp_path / "repo"
    source = repo / "data" / "paper.duckdb"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"do not copy an active database")
    Path(f"{source}.wal").write_bytes(b"active writer marker")

    manifest = create_snapshot(repo, repo / "data" / "backup", ["data/paper.duckdb"])

    assert manifest["entries"] == [{
        "source": "data/paper.duckdb",
        "status": "BLOCKED_ACTIVE_WAL",
        "reason": "DuckDB WAL exists; source may be active",
    }]
    assert not (repo / "data" / "backup" / "files" / "data" / "paper.duckdb").exists()
    report = verify_restore(repo / "data" / "backup", manifest)
    assert report["outcomes"][0]["status"] == "BLOCKED_ACTIVE_WAL"


def test_wal_created_during_snapshot_removes_partial_copy(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    source = repo / "data" / "paper.duckdb"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"stable-looking database")
    original_open = Path.open

    def open_with_new_wal(path, *args, **kwargs):
        if path == source and args and args[0] == "rb":
            Path(f"{source}.wal").write_bytes(b"writer appeared during copy")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_with_new_wal)
    manifest = create_snapshot(repo, repo / "data" / "backup", ["data/paper.duckdb"])

    entry = manifest["entries"][0]
    assert entry["status"] == "BLOCKED_ACTIVE_WAL"
    assert "appeared during snapshot" in entry["reason"]
    assert not (repo / "data" / "backup" / "files" / "data" / "paper.duckdb").exists()
    assert not (repo / "data" / "backup" / "files" / "data" / "paper.duckdb.partial").exists()


def test_snapshot_rejects_paths_outside_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("not allowed", encoding="utf-8")
    with pytest.raises(BackupError, match="escapes repository root"):
        create_snapshot(repo, repo / "data" / "backup", ["../outside.txt"])
