"""Backups of what cannot be rebuilt, with a verification that actually opens the copies.

What is irreplaceable, in order: the forward evidence (paper ledgers, the OMS journal, the owner's portfolio, the
quote and depth archives, the knowledge store) and the raw lake (hashed source files, immutable, the origin of
everything else). The research, F&O and minute warehouses are rebuildable from the lake and from Kite, slowly, and
are not copied here.

* ``backup`` writes a dated snapshot of the small databases through DuckDB itself (``COPY FROM DATABASE`` on a
  read-only connection), so a copy is a consistent database and never a half-written file. A database a writer
  holds is reported, not copied: run it when the runner and refresh are idle.
* ``mirror_lake`` copies only files the destination lacks or has older (the lake is append-only), and never deletes.
* ``verify`` re-hashes every file against the manifest and opens each database read-only, comparing table row
  counts. A backup that was not verified is a hope.

A backup on the same physical disk protects against deletion and corruption, not against the disk dying; the
manifest records the destination's drive so that is visible.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import date
from pathlib import Path

DATA = Path("data")
#: forward evidence and the owner's own records: small, irreplaceable
TIER_A = ("paper_live.duckdb", "paper_book.duckdb", "paper_zoo.duckdb", "my_portfolio.duckdb", "oms.duckdb",
          "knowledge.duckdb", "quote_archive.duckdb", "depth_archive.duckdb")
SNAPSHOT = re.compile(r"\d{4}-\d{2}-\d{2}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _counts(path: Path) -> dict[str, int]:
    import duckdb
    con = duckdb.connect(str(path), read_only=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' "
            "AND table_type = 'BASE TABLE' ORDER BY 1").fetchall()]
        return {t: int(con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in tables}
    finally:
        con.close()


def copy_database(src: Path, dest: Path) -> None:
    """A consistent copy of a DuckDB database through a read-only connection (raises if a writer holds it)."""
    import duckdb
    if dest.exists():
        dest.unlink()
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"ATTACH '{src.as_posix()}' AS s (READ_ONLY)")
        con.execute(f"ATTACH '{dest.as_posix()}' AS d")
        con.execute("COPY FROM DATABASE s TO d")
    finally:
        con.close()


def backup(dest: Path, *, today: date | None = None, data: Path = DATA, names: tuple[str, ...] = TIER_A,
           keep: int = 7) -> dict:
    day = (today or date.today()).isoformat()
    snap = dest / day
    snap.mkdir(parents=True, exist_ok=True)
    files, skipped = {}, {}
    for name in names:
        src = data / name
        if not src.exists():
            continue
        try:
            copy_database(src, snap / name)
        except Exception as e:                          # noqa: BLE001 - one locked database must not stop the others
            skipped[name] = str(e)[:140]
            (snap / name).unlink(missing_ok=True)
            continue
        files[name] = {"sha256": _sha256(snap / name), "bytes": (snap / name).stat().st_size,
                       "tables": _counts(snap / name)}
    manifest = {"day": day, "dest_drive": dest.drive or str(dest.resolve().anchor), "files": files, "skipped": skipped}
    (snap / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    manifest["pruned"] = prune(dest, keep)
    return manifest


def prune(dest: Path, keep: int) -> list[str]:
    """Remove the oldest dated snapshots beyond ``keep``; only folders named YYYY-MM-DD holding a manifest."""
    snaps = sorted(p for p in dest.iterdir() if p.is_dir() and SNAPSHOT.fullmatch(p.name)
                   and (p / "manifest.json").exists())
    gone = []
    for p in snaps[:-keep] if keep > 0 else []:
        shutil.rmtree(p)
        gone.append(p.name)
    return gone


def verify(snap: Path) -> list[str]:
    """Problems found in a snapshot: a missing or altered file, or a database that does not open to the same counts."""
    try:
        manifest = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["manifest.json missing or unreadable"]
    problems = []
    for name, meta in manifest["files"].items():
        path = snap / name
        if not path.exists():
            problems.append(f"{name}: missing")
            continue
        if _sha256(path) != meta["sha256"]:
            problems.append(f"{name}: hash differs from the manifest")
            continue
        try:
            counts = _counts(path)
        except Exception as e:                          # noqa: BLE001 - an unopenable copy is the finding
            problems.append(f"{name}: does not open ({str(e)[:80]})")
            continue
        if counts != meta["tables"]:
            problems.append(f"{name}: table counts differ from the manifest")
    return problems


def latest_snapshot(dest: Path) -> Path | None:
    snaps = sorted(p for p in dest.iterdir() if p.is_dir() and SNAPSHOT.fullmatch(p.name)) if dest.exists() else []
    return snaps[-1] if snaps else None


def mirror_lake(src: Path, dest: Path, *, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> int:
    """Copy lake files the destination lacks or has older. Never deletes. robocopy exit codes below 8 are success."""
    dest.mkdir(parents=True, exist_ok=True)
    r = run(["robocopy", str(src), str(dest), "/E", "/XO", "/R:1", "/W:1", "/NFL", "/NDL", "/NP", "/MT:4"],
            capture_output=True, text=True)
    return int(r.returncode)
