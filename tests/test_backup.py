from datetime import date
from pathlib import Path

import duckdb

from financial_brain import backup as BK


def _db(path: Path, rows: int) -> None:
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE trades (id INTEGER, px DOUBLE)")
    con.execute(f"INSERT INTO trades SELECT i, i * 1.5 FROM range({rows}) t(i)")
    con.close()


def test_backup_copies_consistent_databases_and_verifies(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _db(data / "paper_live.duckdb", 50)
    out = BK.backup(tmp_path / "bk", today=date(2026, 11, 4), data=data, names=("paper_live.duckdb", "absent.duckdb"))
    assert list(out["files"]) == ["paper_live.duckdb"] and out["files"]["paper_live.duckdb"]["tables"] == {"trades": 50}
    assert BK.verify(tmp_path / "bk" / "2026-11-04") == []


def test_verify_catches_an_altered_or_missing_copy(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _db(data / "paper_live.duckdb", 10)
    BK.backup(tmp_path / "bk", today=date(2026, 11, 4), data=data, names=("paper_live.duckdb",))
    snap = tmp_path / "bk" / "2026-11-04"
    with (snap / "paper_live.duckdb").open("ab") as f:
        f.write(b"x")
    assert "hash differs" in BK.verify(snap)[0]
    (snap / "paper_live.duckdb").unlink()
    assert BK.verify(snap) == ["paper_live.duckdb: missing"]
    assert BK.verify(tmp_path / "nowhere")[0].startswith("manifest.json")


def test_a_database_a_writer_holds_is_reported_and_the_others_still_copy(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _db(data / "a.duckdb", 5)
    _db(data / "b.duckdb", 7)
    writer = duckdb.connect(str(data / "a.duckdb"))      # holds the file open for writing
    try:
        out = BK.backup(tmp_path / "bk", today=date(2026, 11, 4), data=data, names=("a.duckdb", "b.duckdb"))
    finally:
        writer.close()
    assert "a.duckdb" in out["skipped"] and "b.duckdb" in out["files"]
    assert not (tmp_path / "bk" / "2026-11-04" / "a.duckdb").exists()


def test_prune_keeps_the_newest_snapshots_and_touches_nothing_else(tmp_path):
    dest = tmp_path / "bk"
    for d in ("2026-11-01", "2026-11-02", "2026-11-03"):
        (dest / d).mkdir(parents=True)
        (dest / d / "manifest.json").write_text("{}")
    (dest / "notes").mkdir()
    (dest / "2026-10-01").mkdir()                        # a dated folder with no manifest is not ours to delete
    assert BK.prune(dest, 2) == ["2026-11-01"]
    assert (dest / "notes").exists() and (dest / "2026-10-01").exists() and (dest / "2026-11-03").exists()


def test_mirror_lake_never_deletes_and_treats_robocopy_codes_below_8_as_success(tmp_path):
    seen = {}

    def fake(cmd, **kw):
        seen["cmd"] = cmd

        class R:
            returncode = 1                               # robocopy: files copied
        return R()

    assert BK.mirror_lake(tmp_path / "lake", tmp_path / "mirror", run=fake) == 1
    assert "/MIR" not in seen["cmd"] and "/PURGE" not in seen["cmd"] and "/XO" in seen["cmd"]
