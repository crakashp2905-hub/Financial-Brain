"""One read-only, fail-closed status view for the single-trader research workflow."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb

from ..agents import brain
from ..evaluation import prospective
from ..paper import reconcile
from . import snapshot

IST = timezone(timedelta(hours=5, minutes=30))


def _holdings(data_root: Path, today: date) -> dict:
    path = data_root / "my_portfolio.duckdb"
    if not path.is_file():
        return {"status": "UNAVAILABLE"}
    try:
        with duckdb.connect(str(path), read_only=True) as con:
            exists = con.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'holdings_snap'").fetchone()
            if not exists:
                return {"status": "UNAVAILABLE"}
            day = con.execute("SELECT MAX(day) FROM holdings_snap").fetchone()[0]
            if day is None:
                return {"status": "EMPTY"}
            rows = con.execute("""SELECT tradingsymbol, quantity, last_price, pnl
                                  FROM holdings_snap WHERE day = ? AND quantity > 0
                                  ORDER BY tradingsymbol""", [day]).fetchall()
    except duckdb.Error as exc:
        return {"status": "UNREADABLE", "reason": str(exc)}
    age = (today - day).days
    return {"status": "CURRENT_SNAPSHOT" if 0 <= age <= 4 else "STALE_SNAPSHOT",
            "asof": day.isoformat(), "age_days": age, "count": len(rows),
            "positions": [{"symbol": r[0], "quantity": r[1], "last_price": r[2],
                           "broker_reported_pnl": r[3]} for r in rows],
            "note": "Saved broker snapshot only; not a live broker reconciliation."}


def status(data_root: Path, *, today: date | None = None, limit: int = 20) -> dict:
    """Inspect only local artifacts; never fetch quotes, approve risk or submit orders."""
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    today = today or datetime.now(IST).date()
    path = data_root / "terminal" / "opportunities.json"
    try:
        rows, manifest = snapshot.read(path)
    except snapshot.SnapshotError as exc:
        return {"schema": 1, "status": "ABSTAIN", "asof": None,
                "reason": str(exc), "decisions": [], "execution_allowed": False}
    kill_path = data_root / "killswitch.json"
    try:
        kill_state = json.loads(kill_path.read_text(encoding="utf-8")) if kill_path.is_file() else {}
    except (OSError, ValueError):
        kill_state = {}
    if not isinstance(kill_state, dict):
        kill_state = {}
    try:
        decisions = brain.run(rows, kill_state, today=today, limit=limit)
    except (TypeError, ValueError) as exc:
        return {"schema": 1, "status": "ABSTAIN", "asof": manifest.get("asof"),
                "reason": f"invalid opportunity content: {exc}", "decisions": [],
                "execution_allowed": False}
    ledger_path = data_root / "paper_live.duckdb"
    evidence = {"status": "LEDGER_UNAVAILABLE"}
    paper = {"status": "LEDGER_UNAVAILABLE"}
    if ledger_path.is_file():
        try:
            with duckdb.connect(str(ledger_path), read_only=True) as con:
                evidence = prospective.audit(con)
                paper_result = reconcile.compare(con, start=today - timedelta(days=30), end=today)
                paper = {"status": paper_result["status"],
                         "paired": paper_result["counts"].get("paired", 0),
                         "same_days": paper_result["same_days"]}
        except (duckdb.Error, ValueError) as exc:
            evidence = {"status": "LEDGER_UNREADABLE", "reason": str(exc)}
            paper = {"status": "LEDGER_UNREADABLE"}
    holdings = _holdings(data_root, today)
    gaps = []
    if kill_state.get("level") not in {"OK", "REDUCE", "HALT"}:
        gaps.append("kill-switch state missing or invalid")
    if not evidence.get("resolved_attested_days"):
        gaps.append("no resolved forward-attested champion cohort")
    if not paper.get("paired"):
        gaps.append("no same-day paired live/replay paper trades")
    if holdings.get("status") not in {"CURRENT_SNAPSHOT", "EMPTY"}:
        gaps.append("saved holdings unavailable or stale")
    if all(v == 0 for v in decisions["by_verdict"].values()):
        gaps.append("no opportunity decisions in snapshot")
    return {"schema": 1, "status": "RESEARCH_ONLY", "asof": manifest["asof"],
            "published_at": manifest.get("published_at"), "snapshot_sha256": manifest["sha256"],
            "snapshot_rows": manifest["rows"], "kill_switch": kill_state.get("level", "UNKNOWN"),
            "by_verdict": decisions["by_verdict"], "decisions": decisions["decisions"],
            "holdings": holdings, "evidence_gaps": gaps,
            "champion_evidence": evidence, "paper_reconciliation": paper,
            "execution_allowed": False,
            "note": "Research view only. Modelled returns are not verified trading edges."}
