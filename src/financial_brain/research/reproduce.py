"""One command to recreate a trial: its script, the code commit, the environment lockfile and a fingerprint of the
data it read.

    fb reproduce --list                 every trial with a script
    fb reproduce "<ledger name>"        fingerprint the inputs, then run the trial's script (results land where the
                                        script writes them; the ledger is never written - a trial is spent once)
    fb reproduce "<name>" --fingerprint-only

A fingerprint is, per input table, the row count, the last date and an order-independent hash of every value
(``bit_xor(hash(sha256(to_json(ROW(...)))))`` on complete rows). DuckDB's plain row
hash combines columns in a way that can miss value swaps between dates, so the row is
serialized and SHA-256-hashed first. Two runs on the same commit, lockfile and fingerprint must give the same
numbers; seeds are fixed in the code. ``standard.record`` stamps each new trial with the commit and the lockfile hash,
and ``manifest`` adds the fingerprint, so a later run can tell *which* of the three changed.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

LOCK = Path("requirements-lock.txt")
SCRIPTS = Path("scripts") / "trials"

#: ledger name -> (script, research tables it reads)
TRIALS = {
    "M1 cost-aware momentum book": ("m1_momentum_book.py", ["adjusted_prices", "features"]),
    "F3 short build-up avoid filter": ("f3_short_buildup.py", ["adjusted_prices", "features"]),
    "C1 rich-basis carry": ("batch_three_c1_n2f_eap.py", ["adjusted_prices", "features", "announcements"]),
    "N2F rejected results as hedged futures short": ("batch_three_c1_n2f_eap.py", ["adjusted_prices", "features", "announcements"]),
    "EAP earnings-announcement premium in futures": ("batch_three_c1_n2f_eap.py", ["adjusted_prices", "features", "announcements"]),
    "BAB betting against beta in futures": ("bab_futures.py", ["adjusted_prices", "features"]),
    "K7 chart and candle setups in futures (17-setup family)": ("k7_chart_setups.py", ["adjusted_prices", "features"]),
    "D1 delivery share long-short in futures": ("d1_delivery_share.py", ["adjusted_prices", "features", "delivery"]),
    "B4 index mean reversion and VWAP (9-rule family)": ("b4_index_rules.py", ["index_levels"]),
    "A10 published price anomalies (16-anomaly family)": ("a10_price_anomalies.py", ["adjusted_prices", "features"]),
    "X3 earnings surprise drift out of sample": ("x3_mf_fundamentals.py", ["financial_results", "xbrl_facts", "adjusted_prices", "features", "eod_prices"]),
    "MF multi-factor quality value low-risk book": ("x3_mf_fundamentals.py", ["financial_results", "xbrl_facts", "adjusted_prices", "features", "eod_prices"]),
    "I/S intraday and swing family on minute bars (9 rules)": ("is_minute_family.py", ["adjusted_prices"]),
}
DATE_COLS = ("business_date", "filed_at", "period_end", "d")
TRIAL_FILES = {
    "f3_short_buildup.py": ("data/futures_history.duckdb",),
    "batch_three_c1_n2f_eap.py": ("data/basis_contracts.parquet",),
    "bab_futures.py": ("data/basis_contracts.parquet",),
    "k7_chart_setups.py": ("data/basis_contracts.parquet",),
    "d1_delivery_share.py": ("data/basis_contracts.parquet",),
    "b4_index_rules.py": ("data/edges/nifty50_minute.parquet",),
    "a10_price_anomalies.py": ("data/edges/nifty50_minute.parquet",),
    "is_minute_family.py": ("data/basis_contracts.parquet", "data/minute_history.duckdb"),
}


def git_commit(repo: Path = Path(".")) -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def lock_sha(path: Path = LOCK) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.exists() else None


def fingerprint(rcon, tables: list[str]) -> dict:
    out = {}
    for t in tables:
        cols = rcon.execute("SELECT column_name, data_type FROM information_schema.columns "
                            "WHERE table_name = ? ORDER BY ordinal_position", [t]).fetchall()
        if not cols:
            out[t] = {"missing": True}
            continue
        names = [c for c, _ in cols]
        fields = ", ".join('"' + c.replace('"', '""') + '"' for c in names)
        row = rcon.execute(f"SELECT COUNT(*), bit_xor(hash(sha256(to_json(ROW({fields}))))) FROM {t}").fetchone()
        h = int(row[1]) if row[1] is not None else 0
        dc = next((c for c in DATE_COLS if c in names), None)
        last = rcon.execute(f"SELECT MAX({dc}) FROM {t}").fetchone()[0] if dc else None
        out[t] = {"rows": int(row[0]), "last": str(last) if last is not None else None, "schema": cols,
                  "hash": f"{h:016x}"}
    return out


def manifest(rcon, name: str) -> dict:
    script, tables = TRIALS[name]
    files = {}
    for location in TRIAL_FILES.get(script, ()):
        path = Path(location)
        # Full hashes of the 40 GB minute warehouse would make an ordinary manifest expensive.
        # File metadata is an inventory, explicitly weaker than a retained immutable snapshot.
        stat = path.stat() if path.exists() else None
        files[location] = ({"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                            "verified_snapshot": False} if stat else {"missing": True})
    return {"trial": name, "script": str(SCRIPTS / script), "commit": git_commit(), "lockfile": lock_sha(),
            "data": fingerprint(rcon, tables), "external_inputs": files}


def run_script(name: str) -> int:
    import os
    import sys
    script = SCRIPTS / TRIALS[name][0]
    env = {**os.environ, "PYTHONPATH": "src"}
    return subprocess.run([sys.executable, "-u", str(script)], env=env).returncode
