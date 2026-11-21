"""Machine-readable baseline capture (FE-0001).

Records git HEAD, branch, working-tree state, SHA-256 of the sources the forward-edge program reads, top-level
dataset inventory (names and sizes only - nothing is opened, hashed in bulk or modified), presence of named
credentials as booleans (values are never read into the record), and missing prerequisites. Open PRs are queried
through ``gh`` only when requested, because that needs network and credentials.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SOURCES = (
    "README.md",
    "docs/READINESS.md",
    "docs/adr/0004-one-evidence-standard.md",
    "docs/TRADING-EDGE-CHECKLIST-STATUS-2026-10-30.md",
    "src/financial_brain/evaluation/champion.py",
    "src/financial_brain/evaluation/prospective.py",
    "src/financial_brain/paper/intraday_runner.py",
    "src/financial_brain/paper/engine.py",
    "src/financial_brain/kernel/events.py",
    "src/financial_brain/kernel/oms.py",
    "src/financial_brain/kernel/pilot.py",
    "docs/trials/ledger.jsonl",
)
#: Names only. A value is never read into the baseline.
CREDENTIAL_ENV = ("KITE_API_KEY", "KITE_ACCESS_TOKEN", "FB_TIMESTAMP_PROVIDER", "FB_TRUST_ROOTS")
DATASET_DIRS = ("data", "artifacts", "docs/trials")


def _git(root: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _inventory(root: Path, rel: str) -> dict:
    base = root / rel
    if not base.is_dir():
        return {"present": False}
    files = []
    with os.scandir(base) as it:
        for entry in it:
            if entry.is_file():
                files.append({"name": entry.name, "bytes": entry.stat().st_size})
    files.sort(key=lambda f: f["name"])
    return {"present": True, "top_level_files": len(files), "top_level_bytes": sum(f["bytes"] for f in files)}


def _open_prs(root: Path) -> list[dict] | str:
    try:
        done = subprocess.run(["gh", "pr", "list", "--state", "open", "--json", "number,title,headRefName"],
                              cwd=root, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "gh_unavailable"
    if done.returncode != 0:
        return "gh_failed"
    return json.loads(done.stdout or "[]")


def capture_baseline(root: Path, *, now: datetime, include_prs: bool = False) -> dict:
    root = Path(root)
    head = _git(root, "rev-parse", "HEAD")
    status = _git(root, "status", "--porcelain") or ""
    lines = [line for line in status.splitlines() if line.strip()]
    sources = {}
    for rel in SOURCES:
        path = root / rel
        sources[rel] = {"present": path.is_file(), "sha256": _sha256(path) if path.is_file() else None}
    missing = [f"env:{name}" for name in CREDENTIAL_ENV if not os.environ.get(name)]
    missing += [f"source:{rel}" for rel, info in sources.items() if not info["present"]]
    if not (root / "data" / "financial_brain.duckdb").is_file():
        missing.append("dataset:data/financial_brain.duckdb")
    if not (root / "docs" / "forward-edge" / "trust-roots.json").is_file():
        missing.append("custody:trusted_timestamp_roots_not_pinned")
    return {
        "schema": "fb.forward_edge.baseline.v1",
        "captured_at_utc": now.astimezone(timezone.utc).isoformat(),
        "git": {
            "head": head,
            "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD"),
            "dirty_entries": len(lines),
            "untracked_or_modified": lines[:200],
        },
        "open_pull_requests": _open_prs(root) if include_prs else "not_queried",
        "sources": sources,
        "datasets": {rel: _inventory(root, rel) for rel in DATASET_DIRS},
        "credentials_present": {name: bool(os.environ.get(name)) for name in CREDENTIAL_ENV},
        "missing_prerequisites": sorted(missing),
        "runtime": {"python": sys.version.split()[0], "platform": platform.system()},
    }


def write_baseline(root: Path, out: Path, *, now: datetime | None = None, include_prs: bool = False) -> dict:
    record = capture_baseline(root, now=now or datetime.now(timezone.utc), include_prs=include_prs)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record
